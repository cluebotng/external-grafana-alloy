#!/usr/bin/env python3
import json
import os
import sys
from dataclasses import dataclass
from pathlib import PosixPath
from typing import Optional, List, Union, Dict


@dataclass
class RemoteConfig:
    url: str
    username: Optional[str]
    password: Optional[str]


@dataclass
class TargetConfig:
    host: str
    port: int
    interval: str
    timeout: str
    path: Optional[str]


@dataclass
class JobConfig:
    name: str
    interval: str = "60s"
    timeout: str = "60s"
    path: Optional[str] = None

    @property
    def safe_name(self) -> str:
        return self.name.replace("-", "_")


@dataclass
class DiscoverConfig:
    role: str
    jobs: List[JobConfig]


def get_remote_config() -> Optional[RemoteConfig]:
    if remote_url := os.environ.get("ALLOY_REMOTE_URL"):
        return RemoteConfig(
            url=remote_url,
            username=os.environ.get("ALLOY_REMOTE_USERNAME"),
            password=os.environ.get("ALLOY_REMOTE_PASSWORD"),
        )
    return None


def get_targets_config() -> List[Union[TargetConfig, DiscoverConfig]]:
    targets = []
    if scrape_targets := os.environ.get("ALLOY_SCRAPE_TARGETS"):
        for target in json.loads(scrape_targets):
            match target.get("type", "target"):
                case "target":
                    targets.append(
                        TargetConfig(
                            host=target["host"],
                            port=target["port"],
                            interval=target.get("interval", "60s"),
                            timeout=target.get("timeout", "60s"),
                            path=target.get("path"),
                        )
                    )
                case "discover":
                    targets.append(
                        DiscoverConfig(
                            role=target.get("role", "pod"),
                            jobs=[
                                JobConfig(
                                    name=job["name"],
                                    interval=job.get("interval", "60s"),
                                    timeout=job.get("timeout", "60s"),
                                    path=job.get("path"),
                                )
                                for job in target.get("jobs", [])
                            ],
                        )
                    )
    return targets


def write_config(
    config_path: PosixPath,
    target_configs: List[TargetConfig],
    remote_config: RemoteConfig,
    debug: bool = False,
) -> None:
    config = ""
    if debug:
        config += 'logging {\n    level="debug"\n}\n'
        config += "livedebugging {\n    enabled=true\n}\n"

    # Scrape targets
    for target_config in target_configs:
        if isinstance(target_config, TargetConfig):
            clean_name = target_config.host.replace("-", "_")
            config += (
                f'prometheus.scrape "target_{clean_name}_{target_config.port}" {{\n'
            )
            config += f'    targets = [{{__address__ = "{target_config.host}:{target_config.port}"}}]\n'
            config += f'    scrape_interval = "{target_config.interval}"\n'
            config += f'    scrape_timeout = "{target_config.timeout}"\n'
            if target_config.path:
                config += f'    metrics_path = "{target_config.path}"\n'
            config += "    forward_to = [prometheus.remote_write.default.receiver]\n"
            config += "}\n"

        if isinstance(target_config, DiscoverConfig):
            # Container service tokens don't have any access to the API
            # So mount NFS and use the tool creds...
            kubeconfig = None
            namespace = None
            if tool_data_dir := os.environ.get("TOOL_DATA_DIR"):
                namespace = f'tool-{tool_data_dir.split("/")[-1]}'
                kubeconfig_file = PosixPath(tool_data_dir) / ".kube" / "config"
                if kubeconfig_file.is_file():
                    kubeconfig = kubeconfig_file.as_posix()

            # Discover running pods (jobs)
            config += f'discovery.kubernetes "{target_config.role}" {{\n'
            config += f'    role = "{target_config.role}"\n'
            if namespace:
                config += "    namespaces {\n"
                config += f'        names = ["{namespace}"]\n'
                config += "    }\n"
            if kubeconfig:
                config += f'    kubeconfig_file = "{kubeconfig}"\n'
            config += "}\n"

            target_jobs = (
                [JobConfig(name="all")]
                if not target_config.jobs
                else target_config.jobs
            )
            for target_job in target_jobs:
                if target_job != "all":
                    config += f'discovery.relabel "{target_config.role}_{target_job.safe_name}" {{\n'
                    config += f"    targets = discovery.kubernetes.{target_config.role}.targets\n"
                    config += "    rule {\n"
                    config += (
                        '        source_labels = ["__meta_kubernetes_pod_label_name"]\n'
                    )
                    config += f'        regex = "{target_job.name}"\n'
                    config += '        action = "keep"\n'
                    config += "    }\n"
                    config += "}\n"

            for target_job in target_jobs:
                # Scrape the discovered pods (jobs)
                config += f'prometheus.scrape "{target_config.role}_{target_job.safe_name}" {{\n'
                if target_job == "all":
                    config += f"    targets = discovery.kubernetes.{target_config.role}.targets\n"
                else:
                    config += f"    targets = discovery.relabel.{target_config.role}_{target_job.safe_name}.output\n"
                config += f'    scrape_interval = "{target_job.interval}"\n'
                config += f'    scrape_timeout = "{target_job.timeout}"\n'
                if target_job.path:
                    config += f'    metrics_path = "{target_job.path}"\n'
                config += (
                    "    forward_to = [prometheus.remote_write.default.receiver]\n"
                )
                config += "}\n"

    # Remote write
    config += 'prometheus.remote_write "default" {\n'
    config += "  endpoint {\n"
    config += f'    url = "{remote_config.url}"\n'
    if remote_config.username and remote_config.password:
        config += "    basic_auth {\n"
        config += f'      username = "{remote_config.username}"\n'
        config += f'      password = "{remote_config.password}"\n'
        config += "    }\n"
    config += "  }\n"
    config += "}\n"

    with config_path.open("w") as fh:
        fh.write(config)


def run_alloy(config_path: PosixPath):
    # Note: We replace the current process, rather than running as a subprocess,
    #      so `alloy` is essentially being run from the launcher.
    binary_path = PosixPath(__file__).parent / "alloy"
    if not binary_path.is_file():
        print("Missing binary!")
        sys.exit(2)

    return os.execv(
        binary_path.as_posix(),
        [
            binary_path.as_posix(),
            "run",
            "--cluster.enabled=false",  # No clustering
            "--disable-reporting=true",  # Just in case this breaks the privacy policy
            "--server.http.listen-addr=0.0.0.0:8118",  # For health checking
            "--storage.path=/tmp/data",  # Container storage, no real persistence
            config_path.as_posix(),  # The config we wrote earlier
        ],
    )


def main():
    remote_config = get_remote_config()
    if not remote_config:
        print("No remote config found!")
        sys.exit(1)

    target_configs = get_targets_config()
    if not target_configs:
        print("No targets found!")
        sys.exit(1)

    config_path = PosixPath("/tmp/config.alloy")
    write_config(config_path, target_configs, remote_config)
    run_alloy(config_path)


if __name__ == "__main__":
    main()

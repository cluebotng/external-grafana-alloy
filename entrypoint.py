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
    label: str = "__meta_kubernetes_pod_label_app_kubernetes_io_name"
    path: Optional[str] = None

    @property
    def safe_name(self) -> str:
        return self.name.replace("-", "_")


@dataclass
class DiscoverConfig:
    role: str
    jobs: List[JobConfig]


def get_local_config() -> Optional[RemoteConfig]:
    if os.environ.get("ALLOY_SEND_TO_LOCAL_CLUSTER", "true") == "true":
        return RemoteConfig(
            url="http://prometheus.tool-cluebotng-monitoring.svc.tools.local:9090/api/v1/write",
            username=os.environ.get("ALLOY_LOCAL_USERNAME"),
            password=os.environ.get("ALLOY_LOCAL_PASSWORD"),
        )
    return None


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
                                    label=job.get(
                                        "label",
                                        "__meta_kubernetes_pod_label_app_kubernetes_io_name",
                                    ),
                                    interval=job.get("interval", "60s"),
                                    timeout=job.get("timeout", "60s"),
                                    path=job.get("path"),
                                )
                                for job in target.get("jobs", [])
                            ],
                        )
                    )
    return targets


def get_kubernetes_namespace() -> Optional[str]:
    namespace_file = PosixPath(
        "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
    )
    if namespace_file.is_file():
        with namespace_file.open("r") as fh:
            return fh.read().strip()
    return None


def get_tool_name(namespace: Optional[str]) -> Optional[str]:
    if namespace and namespace.startswith('tool-'):
        return namespace.split('tool-')[1]
    return None


def write_config(
    config_path: PosixPath,
    target_configs: List[TargetConfig],
    remotes: Dict[str, RemoteConfig],
    debug: bool = False,
) -> None:
    config = ""
    if debug:
        config += 'logging {\n    level="debug"\n}\n'
        config += "livedebugging {\n    enabled=true\n}\n"

    kubernetes_namespace = get_kubernetes_namespace()
    tool_name = get_tool_name(kubernetes_namespace)
    forward_to = [f"prometheus.remote_write.{name}.receiver" for name in remotes.keys()]

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
            if tool_name:
                config += '    labels {\n'
                config += f'        source_tool = "{tool_name}"\n'
                config += '    }\n'
            config += f"    forward_to = [{', '.join(forward_to)}]\n"
            config += "}\n"

        if isinstance(target_config, DiscoverConfig):
            # Container service tokens don't have any access to the API
            # So mount NFS and use the tool creds...
            kubeconfig = None
            if tool_data_dir := os.environ.get("TOOL_DATA_DIR"):
                kubeconfig_file = PosixPath(tool_data_dir) / ".kube" / "config"
                if kubeconfig_file.is_file():
                    kubeconfig = kubeconfig_file.as_posix()

            # Discover running pods (jobs)
            config += f'discovery.kubernetes "{target_config.role}" {{\n'
            config += f'    role = "{target_config.role}"\n'
            if kubernetes_namespace:
                config += "    namespaces {\n"
                config += f'        names = ["{kubernetes_namespace}"]\n'
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
                if target_job.name != "all":
                    config += f'discovery.relabel "{target_config.role}_{target_job.safe_name}" {{\n'
                    config += f"    targets = discovery.kubernetes.{target_config.role}.targets\n"
                    config += "    rule {\n"
                    # Note: Depending on how the pod was created (webservice vs job) this is different
                    config += f'        source_labels = ["{target_job.label}"]\n'
                    config += f'        regex = "{target_job.name}"\n'
                    config += '        action = "keep"\n'
                    config += "    }\n"
                    config += "}\n"

            for target_job in target_jobs:
                # Scrape the discovered pods (jobs)
                scrape_prefix = (
                    f'{kubernetes_namespace.replace("tool-", "").replace("-", "_")}_'
                    if kubernetes_namespace
                    else ""
                )
                config += f'prometheus.scrape "{scrape_prefix}{target_config.role}_{target_job.safe_name}" {{\n'
                if target_job.name == "all":
                    config += f"    targets = discovery.kubernetes.{target_config.role}.targets\n"
                else:
                    config += f"    targets = discovery.relabel.{target_config.role}_{target_job.safe_name}.output\n"
                config += f'    scrape_interval = "{target_job.interval}"\n'
                config += f'    scrape_timeout = "{target_job.timeout}"\n'
                if target_job.path:
                    config += f'    metrics_path = "{target_job.path}"\n'
                config += f"    forward_to = [{', '.join(forward_to)}]\n"
                config += "}\n"

    # Remote write
    for name, remote in remotes.items():
        config += f'prometheus.remote_write "{name}" {{\n'
        config += "  endpoint {\n"
        config += f'    url = "{remote.url}"\n'
        if remote.username and remote.password:
            config += "    basic_auth {\n"
            config += f'      username = "{remote.username}"\n'
            config += f'      password = "{remote.password}"\n'
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
    remotes = {}
    if local_config := get_local_config():
        remotes["local"] = local_config
    if remote_config := get_remote_config():
        remotes["remote"] = remote_config
    if not remotes:
        print("No remotes found in config!")
        sys.exit(1)

    target_configs = get_targets_config()
    if not target_configs:
        print("No targets found!")
        sys.exit(1)

    config_path = PosixPath("/tmp/config.alloy")
    write_config(config_path, target_configs, remotes)
    run_alloy(config_path)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import shutil
import subprocess
from pathlib import PosixPath


TARGET_RELEASE = "1.10.1"
TARGET_PATCH = "1"
WORKSPACE_DIR = PosixPath("/workspace")
APT_DIR = PosixPath("/tmp/apt")
DEB_FILE = PosixPath(f"/tmp/alloy-{TARGET_RELEASE}-{TARGET_PATCH}.amd64.deb")


def install_package():
    # We do this in a similar style to the `apt` layer as we cannot do a simple `apt-get install`,
    # basically download the .deb file and extract it directly.
    # Since we are only interested in the 'static' executable a little fernangle is done.

    # Download the .deb
    if not DEB_FILE.is_file():
        subprocess.run(
            [
                "curl",
                "--silent",
                "--show-error",
                "--fail",
                "-L",
                "-o",
                DEB_FILE.as_posix(),
                f"https://github.com/grafana/alloy/releases/download/v{TARGET_RELEASE}/"
                f"alloy-{TARGET_RELEASE}-{TARGET_PATCH}.amd64.deb",
            ],
            check=True,
        )

    # Extract the .deb
    if not (APT_DIR / "usr" / "bin" / "alloy").is_file():
        subprocess.run(
            [
                "dpkg",
                "-x",
                DEB_FILE.as_posix(),
                APT_DIR.as_posix(),
            ],
            check=True,
        )

    # Copy the binary over to the workspace
    WORKSPACE_DIR.mkdir(exist_ok=True)
    shutil.copy(
        (APT_DIR / "usr" / "bin" / "alloy").as_posix(),
        (WORKSPACE_DIR / "alloy").as_posix(),
    )


def cleanup():
    shutil.rmtree(APT_DIR.as_posix())
    DEB_FILE.unlink()


def appease_poetry():
    # Poetry expects a Python package from `setup.py install`, create a minimal one
    package_dir = PosixPath("/workspace/grafana_alloy")
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").open("w").close()


def main():
    install_package()
    cleanup()
    appease_poetry()


if __name__ == "__main__":
    main()

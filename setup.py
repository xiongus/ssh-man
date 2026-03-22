from setuptools import find_packages, setup


setup(
    name="sshman",
    version="1.0.1",
    description="Offline SSH config and tunnel manager for macOS/Linux terminals.",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "sshman=sshman.cli:main",
        ]
    },
)

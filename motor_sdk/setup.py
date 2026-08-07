from setuptools import setup, find_packages

setup(
    name="motor-sdk",
    version="1.0.0",
    description="DC motor control SDK for Orange Pi GPIO via wiringOP gpio CLI",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[],   # no Python deps — uses wiringOP gpio CLI (subprocess)
    extras_require={
        "dev": ["pytest"],
    },
)

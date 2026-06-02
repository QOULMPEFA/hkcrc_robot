from setuptools import setup, find_packages

setup(
    name="stepper-motor-sdk",
    version="1.0.0",
    description="CiA 402 stepper motor control SDK over CANopen / python-can",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "python-can>=4.3",
    ],
    extras_require={
        "dev": ["pytest", "pytest-timeout"],
    },
)

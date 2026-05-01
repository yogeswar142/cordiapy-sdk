from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="cordia",
    version="1.2.2",
    author="Cordia",
    description="Official analytics SDK for Discord bots using Cordia",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/your-org/cordia-py",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.8",
    install_requires=[
        "aiohttp>=3.8.0",
    ],
)

"""ODC v4 — installable Python package.

Production install:
  pip install -e .

Development install with all extras:
  pip install -e ".[dev,test,docs]"
"""
from setuptools import setup, find_packages
from pathlib import Path

# Read README
readme_path = Path(__file__).parent / "README.md"
long_description = readme_path.read_text() if readme_path.exists() else ""

setup(
    name="odc-v4",
    version="4.0.0",
    description="ODC v4 — Open Digital Companion, a self-improving LLM agent",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Grupo AN Development",
    license="MIT",
    url="https://github.com/GrupoANDevelopment-m/odc-v4",
    packages=find_packages(exclude=[
        "tests", "tests.*",
        "third_party", "third_party.*",
        "docs", "docs.*",
    ]),
    python_requires=">=3.11",
    install_requires=[
        "pyyaml>=6.0.1",
        "python-dotenv>=1.0.0",
        "pydantic>=2.5.0",
        "jsonschema>=4.20.0",
        "requests>=2.31.0",
        "httpx>=0.25.0",
        "beautifulsoup4>=4.12.0",
        "lxml>=4.9.0",
        "playwright>=1.40.0",
        "openai>=1.30.0",
        "anthropic>=0.20.0",
        "google-generativeai>=0.3.0",
        "reportlab>=4.0.0",
    ],
    extras_require={
        "dev": [
            "ruff==0.1.9",
            "mypy==1.7.1",
            "black==23.12.0",
        ],
        "test": [
            "pytest>=7.4.0",
            "pytest-asyncio>=0.23.0",
            "pytest-cov>=4.1.0",
            "pytest-xdist>=3.5.0",
            "pytest-benchmark>=4.0.0",
            "hypothesis>=6.90.0",
        ],
        "docs": [
            "sphinx>=7.0.0",
            "sphinx-rtd-theme>=1.3.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "odc=odc.cli:main",
            "odc-server=odc.server:main",
        ],
    },
    include_package_data=True,
    package_data={
        "odc": ["py.typed"],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Operating System :: POSIX :: Linux",
        "Operating System :: MacOS",
    ],
)

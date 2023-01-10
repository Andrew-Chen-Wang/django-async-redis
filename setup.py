#!/usr/bin/env python

"""The setup script."""

from setuptools import find_packages, setup


with open("README.rst") as readme_file:
    readme = readme_file.read()

with open("HISTORY.rst") as history_file:
    history = history_file.read()

requirements = ["django>=3.2", "redis>=4.2.0"]

setup_requirements = []

test_requirements = []

setup(
    author="Andrew Chen Wang",
    author_email="acwangpython@gmail.com",
    python_requires=">=3.7",
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Natural Language :: English",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Framework :: Django :: 3.2",
        "Framework :: Django :: 4.1",
    ],
    description="Full featured async Redis cache backend for Django.",
    install_requires=requirements,
    license="Apache Software License 2.0",
    long_description=readme + "\n\n" + history,
    include_package_data=True,
    keywords="django-async-redis",
    name="django-async-redis",
    packages=find_packages(include=["django_async_redis", "django_async_redis.*"]),
    setup_requires=setup_requirements,
    test_suite="tests",
    tests_require=test_requirements,
    url="https://github.com/Andrew-Chen-Wang/django-async-redis",
    version="0.2.0",
    zip_safe=False,
)

import setuptools
import re

def get_property(prop, project):
    result = re.search(r'{}\s*=\s*[\'"]([^\'"]*)[\'"]'.format(prop),
        open(project + '/__init__.py').read())
    return result.group(1)

with open("README.md", "r") as fh:
    long_description = fh.read()

PROJECT="adif_merge"

setuptools.setup(
    name=PROJECT,
    version=get_property('__VERSION__', PROJECT),
    author="Paul Traina",
    author_email="bulk+pypi@pst.org",
    description="Amateur Radio ADIF compatible log file merge and resolution",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/pleasantone/adif_merge",
    packages=setuptools.find_packages(),
    entry_points={
        "console_scripts": [
            "adif_merge=adif_merge:main"
        ]
    },
    install_requires=[
        "adif_io"
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)",
        "Operating System :: OS Independent",
        "Development Status :: 4 - Beta",
        "Environment :: Console",
    ],
    python_requires=">=3.6",
)

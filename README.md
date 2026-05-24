# Building Biological Web Applications Using Django

**Author:** Vasarė Petrulaitytė

Django-based web application for bioinformatics sequence analysis, providing a browser interface for HMMER, multiple sequence alignment, and sequence utility tools.

## Features

### HMMER Tools

- **hmmbuild** - build an HMM profile from a multiple sequence alignment
- **hmmsearch** - search sequences against an HMM profile
- **hmmemit** - generate sequences from an HMM profile
- Pfam and InterPro database integration with autocomplete search

### MSA Tools

- **Clustal Omega**, **MAFFT**, **MUSCLE**, and **Kalign** - multiple sequence alignment
- **Format conversion** - convert between alignment formats

### Sequence Utilities

- **FASTA validation** - check sequence format and content
- **FASTA cleaning** - normalize and clean FASTA files

### Workflows

- Chain multiple tools into automated pipelines
- Pass outputs between steps automatically

### Platform

- Asynchronous task execution with Celery and Redis
- Project management with three visibility levels: Private, Link, and Public
- Project sharing with users
- Run history and result storage
- Automatic cleanup of temporary files

## Requirements

### 1. Python 3.10+

Download Python from:

https://www.python.org/downloads/

Check the installed version:

```bash
python3 --version
```

### 2. Redis

**macOS:**

```bash
brew install redis
```

**Ubuntu/Debian:**

```bash
sudo apt-get install -y redis-server
```

**Windows:**

Download Redis from:

https://redis.io/docs/getting-started/installation/install-redis-on-windows/

### 3. HMMER 3.4

**macOS:**

```bash
brew install hmmer
```

**Ubuntu/Debian:**

```bash
sudo apt-get install -y hmmer
```

**Windows / conda:**

```bash
conda install -c bioconda hmmer
```

Check the installed version:

```bash
hmmbuild -h
```

### 4. Multiple Sequence Alignment Tools

Required tools:

- **Clustal Omega 1.2.4** (`clustalo`)
- **MAFFT 7.526** (`mafft`)
- **MUSCLE 5.3** (`muscle`)
- **Kalign 3.5.x** (`kalign`)

**macOS:**

```bash
brew install clustal-omega mafft muscle kalign
```

**Ubuntu/Debian:**

```bash
sudo apt-get install -y clustalo mafft muscle kalign
```

**Windows / conda:**

```bash
conda install -c conda-forge -c bioconda clustalo=1.2.4 mafft=7.526 muscle=5.3
```

Check installed versions:

```bash
clustalo --version
```

```bash
mafft --version
```

```bash
muscle --version
```

```bash
kalign --version
```

### 5. Project Dependencies

Django, Celery, Biopython, and other Python packages are installed from `requirements.txt`.

## Installation

Clone the repository:

```bash
git clone https://github.com/vasarine/HMM-Analysis-Tools-Application.git
```

Open the project directory:

```bash
cd HMM-Analysis-Tools-Application
```

Create a virtual environment:

```bash
python3 -m venv venv
```

Activate the virtual environment.

**macOS/Linux:**

```bash
source venv/bin/activate
```

**Windows:**

```bash
venv\Scripts\activate
```

Install Python dependencies:

```bash
pip install -r requirements.txt
```

Apply database migrations:

```bash
python manage.py migrate
```

## Running Services in Separate Terminals

Use four separate terminal windows.

**Terminal 1 - Redis**

```bash
redis-server
```

**Terminal 2 - Django**

```bash
python manage.py runserver
```

**Terminal 3 - Celery worker**

```bash
celery -A biologine_aplikacija worker -l info
```

**Terminal 4 - Celery Beat**

```bash
celery -A biologine_aplikacija beat -l info
```

The application can be used anonymously, but creating an account is required to save projects, share results, and access workflow history.


Only after all the steps application will be available at:
```text
http://localhost:8000/
```

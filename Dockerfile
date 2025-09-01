FROM continuumio/miniconda3:latest

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/conda/envs/api-runner/bin:$PATH

# Update conda and create environment with Python 3.11.13
RUN conda update -n base -c defaults conda && \
    conda create -n api-runner python=3.11.13 -y && \
    conda clean -afy

WORKDIR /app

# Leverage cache: copy only requirements first
COPY requirements.txt /app/requirements.txt

# Activate environment and install dependencies
RUN /opt/conda/envs/api-runner/bin/pip install --upgrade pip && \
    /opt/conda/envs/api-runner/bin/pip install -r /app/requirements.txt

# Now copy your code
COPY . /app

EXPOSE 8080

# Use conda environment's uvicorn
CMD ["/opt/conda/envs/api-runner/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

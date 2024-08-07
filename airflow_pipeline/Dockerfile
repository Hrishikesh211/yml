# Use the official Apache Airflow image as the base
FROM apache/airflow:2.9.3

# USER root

# RUN apt-get update && apt-get install -y supervisor
# COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Switch back to airflow user
# USER airflow

# # Set the working directory
# ENV AIRFLOW_HOME=/opt/airflow

# # Copy the DAGs and requirements
# COPY ./airflow_pipeline/dags ${AIRFLOW_HOME}/dags
# COPY ./airflow_pipeline/requirements.txt /requirements.txt

# Install additional Python packages required
# COPY requirements.txt /requirements.txt
# RUN pip install -r /requirements.txt

# Expose the Airflow webserver port
EXPOSE 8080

# # Set the entrypoint to supervisord
# ENTRYPOINT ["supervisord"]

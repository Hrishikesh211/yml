# Apache Airflow Pipelines for Azure Functions File Upload

This repository contains two Apache Airflow DAGs (Directed Acyclic Graphs) for file uploading: Async pipeline, this seamlessly integrated through a PostgreSQL database.

## Table of Contents
- [Key Features](#key-features)
- [Setup](#setup)
  - [Prerequisites](#prerequisites)
  - [Installation & Configuration](#installation-&-configuration)
- [PostgreSQL Tables](#postgresql-tables)
  - [Flow Status Table](#flow-status-table)
  - [File Status Table](#file-status-table)
- [Usage](#usage)

## Key Features

- **Asynchronous file processing**: The DAG triggers Azure Functions to process files asynchronously, allowing for efficient handling of multiple files concurrently.

## Setup

### Prerequisites

- Python 3.x
- PostgreSQL
- Recommended OS (Linux/Ubuntu..)

### Installation & Configuration:

Please refer to this confluence page: [Confluence](https://kvelld.atlassian.net/wiki/spaces/RMBV/pages/60555269/Apache+Airflow+Guide)

## PostgreSQL Tables

Before running the pipelines, make sure to set up the required PostgreSQL tables: `flow_status`, and `file_status`.

If you created either of these tables for any different project, it is adviced to drop these tables:
```sql
DROP TABLE IF EXISTS public.file_status;
DROP TABLE IF EXISTS public.flow_status;
```

### Flow Status Table

To create the `flow_status` table, run the following SQL statement:

```sql
CREATE TABLE public.flow_status (
    trigger_id SERIAL PRIMARY KEY,
    file_id character varying(50) NOT NULL,
    status character varying(255),
    error_message text,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### File Status Table

To create the `file_status` table, run the following SQL statement:

```sql
CREATE TABLE public.file_status (
    id uuid PRIMARY KEY,
    file_id character varying(50),
    trigger_id INTEGER,
    status_query_get_uri text,
    send_event_post_uri text,
    terminate_post_uri text,
    rewind_post_uri text,
    purge_history_delete_uri text,
    restart_post_uri text,
    suspend_post_uri text,
    resume_post_uri text,
    CONSTRAINT unique_trigger_file UNIQUE (trigger_id, file_id),
    CONSTRAINT fk_trigger FOREIGN KEY (trigger_id) REFERENCES public.flow_status(trigger_id)
);
```
If you want to test manually from backend itself, use the sql command:
```sql
INSERT INTO flow_status (file_id, status)
VALUES ('45', 'file_uploaded');
```

Query to join both `flow_status` and `file_status` tables:
```sql
SELECT 
    fs.trigger_id,
    fs.file_id AS flow_file_id,
    fs.status AS flow_status,
    fs.error_message,
    fs.created_at AS flow_created_at,
    fs.last_updated_at AS flow_last_updated_at,
    fst.id AS file_status_id,
    fst.file_id AS file_status_file_id,
    fst.status_query_get_uri,
    fst.send_event_post_uri,
    fst.terminate_post_uri,
    fst.rewind_post_uri,
    fst.purge_history_delete_uri,
    fst.restart_post_uri,
    fst.suspend_post_uri,
    fst.resume_post_uri
FROM 
    public.flow_status fs
LEFT JOIN 
    public.file_status fst ON fs.trigger_id = fst.trigger_id;
```

## Usage

1. Trigger the desired DAG (`trigger_azure_function_async.py`) manually or wait for it to be scheduled based on the defined `schedule` interval.

2. Monitor the execution of the tasks in the Airflow web UI and check the logs for any errors or issues.

3. The processed data results will be available in the respective tables in the PostgreSQL database.

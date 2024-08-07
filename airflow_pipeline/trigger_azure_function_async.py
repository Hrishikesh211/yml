"""Airflow DAG to trigger Azure Functions based on PostgreSQL data"""

# Standard library imports
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta

# Third-party imports
import requests
from dotenv import load_dotenv

# Airflow imports
from airflow import DAG
from airflow.exceptions import AirflowException
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.operators.python import PythonOperator

load_dotenv()

AZURE_FUNCTIONS_URL = os.getenv("AZURE_FUNCTIONS_URL")
if not AZURE_FUNCTIONS_URL:
    raise ValueError(
        "AZURE_FUNCTIONS_URL is not set in the environment variables")

# Default arguments for the DAG
default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "start_date": datetime(2023, 1, 1),
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

# Define the DAG
dag = DAG(
    "trigger_azure_functions",
    default_args=default_args,
    description="A DAG to trigger Azure Functions based on PostgreSQL data asynchronously",
    schedule=timedelta(minutes=5),
    max_active_runs=1,  # Ensure only one run is active at a time
    max_active_tasks=5,  # Limit concurrent task instances
)


def get_file_properties_from_db():
    """
    Retrieves file properties from PostgreSQL database.

    This function queries the PostgreSQL database to retrieve file properties
    for files that have a status of 'file_uploaded'. It updates the status of
    these files to 'processing' and returns the trigger_id and file_id of the
    selected files.

    Args:
        **kwargs: Additional keyword arguments.

    Returns:
        A list of tuples containing the trigger_id and file_id of the selected files.

    Raises:
        AirflowException: If there is an error retrieving the file properties.

    """
    logging.info("Getting file properties from PostgreSQL")
    pg_hook = PostgresHook(postgres_conn_id="postgres_default")
    conn = pg_hook.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("BEGIN")
            cur.execute(
                """
                UPDATE flow_status
                SET status = 'processing'
                WHERE trigger_id IN (
                    SELECT trigger_id
                    FROM flow_status
                    WHERE status = 'file_uploaded'
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING trigger_id, file_id
            """
            )
            rows = cur.fetchall()
            conn.commit()
            logging.info("Selected %d files for processing", len(rows))
            return rows
    except Exception as e:
        conn.rollback()
        logging.error("Error in get_file_properties_from_db: %s", str(e))
        raise AirflowException(
            f"Failed to get file properties: {str(e)}") from e
    finally:
        conn.close()


def trigger_azure_function(file_id):
    """
    Triggers an Azure function with the given file_id.

    Args:
        file_id (str): The ID of the file to be processed by the Azure function.

    Returns:
        dict or None: If triggered, returns response data dict.
                     If an error occurs during the triggering process, returns None.

    Raises:
        None

    Example:
        response = trigger_azure_function("12345")
        if response is not None:
            print("Azure function triggered successfully!")
            print(response)
        else:
            print("Failed to trigger Azure function.")
    """
    url = AZURE_FUNCTIONS_URL
    headers = {"Content-Type": "application/json"}

    payload = {"file_id": str(file_id)}

    logging.info("Triggering Azure function for file_id %s", file_id)

    try:
        res = requests.post(
            url,
            headers=headers,
            data=json.dumps(payload),
            timeout=60)
        res.raise_for_status()
        response_data = res.json()
        if res.status_code == 202 and "statusQueryGetUri" in response_data:
            logging.info(
                "Successfully triggered Azure function for file_id %s", file_id
            )
            return response_data
        logging.error(
            "Unexpected response from Azure function "
            "for file_id %s: %s - %s",
            file_id,
            response_data["status_code"],
            response_data["error"],
        )
        return {"error": response_data["error"], "status_code": response_data["status_code"]}
    except requests.Timeout:
        logging.error("Request timed out for file_id %s", file_id)
        return {"error": "Request timed out", "status_code": 408}
    except requests.exceptions.HTTPError as e:
        logging.error(
            "HTTP error from the response of triggering Azure function for file_id %s: %s",
            file_id,
            str(e),
        )
        return {"error": str(e), "status_code": res.status_code if res else 500}
    except requests.exceptions.RequestException as e:
        logging.error(
            "Error triggering Azure function for file_id %s: %s",
            file_id,
            str(e))
        return {"error": str(e), "status_code": 500}
    except Exception as e:  # pylint: disable=broad-except
        logging.error(
            "Unexpected error triggering Azure function for file_id %s: %s",
            file_id,
            str(e),
        )
        return {"error": str(e), "status_code": 500}



def process_file_async(file_data):
    """
    Asynchronously processes a file using Azure functions.

    Args:
        file_data (tuple): A tuple containing the trigger_id and file_id.

    Raises:
        AirflowException: If the Azure function fails to trigger or if the processing times out.

    """
    # pylint: disable=too-many-statements, inconsistent-return-statements, too-many-locals
    trigger_id, file_id = file_data
    logging.info("Processing file %s with trigger_id %s", file_id, trigger_id)

    try:
        result = trigger_azure_function(file_id)

        if "error" in result:
            error_message = result.get("error", "Failed to trigger Azure function")
            logging.error(
                "Failed to trigger Azure function for file_id %s with error: %s",
                file_id, error_message
            )
            update_flow_status_in_db(
                trigger_id, file_id, "flow_status", "trigger_failed", error_message
            )
            return

        status_query_get_uri = result.get("statusQueryGetUri")
        send_event_post_uri = result.get("sendEventPostUri")
        terminate_post_uri = result.get("terminatePostUri")
        rewind_post_uri = result.get("rewindPostUri")
        purge_history_delete_uri = result.get("purgeHistoryDeleteUri")
        restart_post_uri = result.get("restart_post_uri")
        suspend_post_uri = result.get("suspendPostUri")
        resume_post_uri = result.get("resumePostUri")

        insert_file_status_in_db(
            trigger_id,
            file_id,
            status_query_get_uri,
            send_event_post_uri,
            terminate_post_uri,
            rewind_post_uri,
            purge_history_delete_uri,
            restart_post_uri,
            suspend_post_uri,
            resume_post_uri,
        )

        interval = 15
        start_time = time.time()
        timeout_in_seconds = 3600
        max_interval = 300
        retry_count = 0 # Number of retries
        max_retries = 10

        while time.time() - start_time < timeout_in_seconds and retry_count < max_retries:
            try:
                logging.info(
                    "Checking the status of file with id %s (attempt %d)",
                    file_id, retry_count + 1
)
                res = requests.get(status_query_get_uri, timeout=30)
                res.raise_for_status()
                response_data = res.json()

                if (
                    res.status_code == 200
                    and response_data["runtimeStatus"] == "Completed"
                ):
                    output = response_data["output"]

                    if(output["status_code"] == 200):
                        logging.info(
                        "Processing completed for file %s with output: %s",
                        file_id, output
)
                        update_flow_status_in_db(
                            trigger_id, file_id, "flow_status", "completed"
                        )
                        return

                    error_message = response_data["output"]["error"]
                    logging.error(
                        "Processing failed for file %s with error: %s",
                        file_id, error_message
                    )
                    update_flow_status_in_db(
                        trigger_id, file_id, "flow_status", "processing_failed", error_message
                    )
                    return
                    

                if response_data["runtimeStatus"] == "Failed":
                    error_message = response_data["output"]["error"]
                    logging.error(
                        "Processing failed for file %s with error: %s",
                        file_id, error_message
                    )
                    update_flow_status_in_db(
                        trigger_id, file_id, "flow_status", "processing_failed", error_message
                    )
                    return

                logging.info(
                        "Processing not completed yet for file %s. Status: %s",
                        file_id, response_data['runtimeStatus']
)
                retry_count += 1
                logging.info("Retrying after %s seconds...", interval)
                time.sleep(interval)
                interval = min(interval * 2, max_interval)  # exponentially increase the interval

            except requests.Timeout:
                logging.error("Request timed out for file_id %s", file_id)
                retry_count += 1
                logging.info("Retrying after %s seconds...", interval)
                time.sleep(interval)
                interval = min(interval * 2, max_interval)  # exponentially increase the interval

            except requests.RequestException as e:
                logging.error(
                    "Request error occurred for file_id %s: %s",
                    file_id, str(e)
)
                retry_count += 1
                logging.info("Retrying after %s seconds...", interval)
                time.sleep(interval)
                interval = min(interval * 2, max_interval)  # exponentially increase the interval

            except Exception as e:   # pylint: disable=broad-except
                logging.error(
                    "Unexpected error occurred while checking the status "
                    "of file %s: %s",
                    file_id, str(e)
                )
                retry_count += 1
                logging.info("Retrying after %s seconds...", interval)
                time.sleep(interval)
                interval = min(interval * 2, max_interval)  # exponentially increase the interval

        logging.error(
            "Max retries reached for file_id %s. Setting status to 'processing_timeout'",
            file_id
)
        update_flow_status_in_db(
            trigger_id, file_id, "flow_status", "processing_timeout"
        )
        raise AirflowException(f"Processing timeout for file_id {file_id}")

    except Exception as e:
        logging.error("Error processing file %s: %s", file_id, str(e))
        update_flow_status_in_db(
            trigger_id,
            file_id,
            "flow_status",
            "processing_error",
            str(e)
        )
        raise AirflowException(f"Error processing file {file_id}: {str(e)}") from e


def process_files(**kwargs):
    """
    Process files asynchronously based on the file properties retrieved from the database.

    Args:
        **kwargs: Additional keyword arguments.

    Returns:
        None
    """
    ti = kwargs["ti"]
    files_properties = ti.xcom_pull(task_ids="get_file_properties_from_db")
    for file_data in files_properties:
        process_file_async(file_data)


# Airflow task to get file properties from PostgreSQL
get_file_properties_task = PythonOperator(
    task_id="get_file_properties_from_db",
    python_callable=get_file_properties_from_db,
    dag=dag,
)

# Task to process files and trigger Azure Functions
process_files_task = PythonOperator(
    task_id="process_files",
    python_callable=process_files,
    dag=dag,
)

# Define the task dependencies
# pylint: disable=pointless-statement
get_file_properties_task >> process_files_task


# pylint: disable=too-many-arguments, too-many-locals
def insert_file_status_in_db(
    trigger_id,
    file_id,
    status_query_get_uri,
    send_event_post_uri,
    terminate_post_uri,
    rewind_post_uri,
    purge_history_delete_uri,
    restart_post_uri,
    suspend_post_uri,
    resume_post_uri,
):
    """
    Inserts or updates a file status record in the database.

    Parameters:
    - trigger_id (str): The ID of the trigger.
    - file_id (str): The ID of the file.
    - status_query_get_uri (str): The URI for querying the status.
    - send_event_post_uri (str): The URI for sending an event.
    - terminate_post_uri (str): The URI for terminating the flow.
    - rewind_post_uri (str): The URI for rewinding the flow.
    - purge_history_delete_uri (str): The URI for purging the flow history.
    - restart_post_uri (str): The URI for restarting the flow.
    - suspend_post_uri (str): The URI for suspending the flow.
    - resume_post_uri (str): The URI for resuming the flow.

    Raises:
    - AirflowException: If there is an error inserting the flow status.

    Returns:
    - None
    """
    pg_hook = PostgresHook(postgres_conn_id="postgres_default")
    conn = pg_hook.get_conn()
    try:
        with conn.cursor() as cur:
            id = str(uuid.uuid4())  # pylint: disable=redefined-builtin
            query = """
            INSERT INTO file_status (
                id, trigger_id, file_id, status_query_get_uri, send_event_post_uri, terminate_post_uri,
                rewind_post_uri, purge_history_delete_uri, restart_post_uri, suspend_post_uri, resume_post_uri
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (trigger_id, file_id) DO UPDATE SET
                status_query_get_uri = EXCLUDED.status_query_get_uri,
                send_event_post_uri = EXCLUDED.send_event_post_uri,
                terminate_post_uri = EXCLUDED.terminate_post_uri,
                rewind_post_uri = EXCLUDED.rewind_post_uri,
                purge_history_delete_uri = EXCLUDED.purge_history_delete_uri,
                restart_post_uri = EXCLUDED.restart_post_uri,
                suspend_post_uri = EXCLUDED.suspend_post_uri,
                resume_post_uri = EXCLUDED.resume_post_uri
            """
            cur.execute(
                query,
                (
                    id,
                    trigger_id,
                    file_id,
                    status_query_get_uri,
                    send_event_post_uri,
                    terminate_post_uri,
                    rewind_post_uri,
                    purge_history_delete_uri,
                    restart_post_uri,
                    suspend_post_uri,
                    resume_post_uri,
                ),
            )
            conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("Error inserting into file_status: %s", str(e))
        raise AirflowException(
            f"Failed to insert file status: {str(e)}") from e
    finally:
        conn.close()


def update_flow_status_in_db(
    trigger_id, file_id, table_name, status, error_message=None
):
    """
    Updates the status of a file in the specified database table.

    Args:
        trigger_id (int): The trigger ID associated with the file.
        file_id (int): The ID of the file.
        table_name (str): The name of the database table.
        status (str): The new status to be set for the file.
        error_message (str, optional): Error message if failed. Defaults to None.

    Raises:
        AirflowException: If there is an error updating the file status.

    """
    pg_hook = PostgresHook(postgres_conn_id="postgres_default")
    conn = pg_hook.get_conn()
    try:
        with conn.cursor() as cur:
            query = f"UPDATE {table_name} SET status = %s, last_updated_at = CURRENT_TIMESTAMP"
            if error_message:
                query += ", error_message = %s WHERE trigger_id::text = %s AND file_id::text = %s"
                cur.execute(
                    query,
                    (status,
                     error_message,
                     str(trigger_id),
                        str(file_id)))
            else:
                query += " WHERE trigger_id::text = %s AND file_id::text = %s"
                cur.execute(query, (status, str(trigger_id), str(file_id)))
            conn.commit()
    except Exception as e:
        conn.rollback()
        logging.error("Error updating flow status: %s", str(e))
        raise AirflowException(
            f"Failed to update flow status: {str(e)}") from e
    finally:
        conn.close()

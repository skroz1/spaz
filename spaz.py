#!/usr/bin/env python3
#
# spaz - Query SensorPush data for Zabbix integration

import typer
import json
import sys
import os
import requests
from requests import session
from dateutil import parser
from loguru import logger
from pathlib import Path
from getpass import getpass
from datetime import datetime, timedelta, timezone
from pyzabbix import ZabbixMetric, ZabbixSender

AUTHORIZATION_VALIDITY = 3600  # 1 hour
ACCESS_VALIDITY = 1800 # 30 minutes
BASE_URL = "https://api.sensorpush.com/api/v1"

config = {}

app = typer.Typer()

# Global variables for config and logger
config = {}
config_path = Path.home() / ".spaz" / "config.json"
log_path = Path.home() / ".spaz" / "log"

# set up logging
def setup_logging():
    # Remove any existing handlers
    logger.remove()
    
    # Add a handler for errors only to stderr
    logger.add(sys.stderr, level="ERROR", format="{time} {level} {message}")

    # Add a file handler for all logs
    log_path.parent.mkdir(exist_ok=True)

def load_config():
    if config_path.exists():
        try:
            with open(config_path, "r") as file:
                return json.load(file)
        except json.JSONDecodeError:
            logger.error("Error decoding config file. Ensure it is valid JSON.")
            return {}
    else:
        return {}

def write_config(config):
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as file:
        json.dump(config, file, indent=4)
    logger.info("Configuration saved.")

def get_authorization_code(email: str, password: str) -> str:
    """Get the authorization code by logging in with email and password."""
    url = f"{BASE_URL}/oauth/authorize"
    payload = {"email": email, "password": password}
    response = requests.post(url, json=payload)
    response.raise_for_status()  # Raises an HTTPError for bad responses
    data = response.json()
    return data["authorization"]

def get_access_token(authorization_code: str) -> str:
    """Request an access token using the authorization code."""
    url = f"{BASE_URL}/oauth/accesstoken"
    payload = {"authorization": authorization_code}
    response = requests.post(url, json=payload)
    response.raise_for_status()
    data = response.json()
    return data["accesstoken"]

def refresh_access_token(refresh_token: str):
    """Refresh the access token using the refresh token."""
    # Placeholder URL and payload - replace with actual SensorPush API details
    url = f"{BASE_URL}/refresh"
    payload = {"refreshToken": refresh_token}
    response = requests.post(url, json=payload)
    response.raise_for_status()
    data = response.json()
    return data["accessToken"], data["refreshToken"]

def is_token_valid(last_updated_str: str, validity_duration: int) -> bool:
    """Check if the token is still valid based on its last updated timestamp."""
    last_updated = datetime.fromisoformat(last_updated_str)
    return datetime.now() - last_updated < timedelta(seconds=validity_duration)

def authenticate () -> bool:
    # Determine the need for new authentication based on token validity
    need_new_auth = True
    if "authorization_token" in config and "auth_last_updated" in config:
        if is_token_valid(config["auth_last_updated"], AUTHORIZATION_VALIDITY):
            need_new_auth = False

    if need_new_auth:
        try:
            authorization_code = get_authorization_code(config["email"], config["password"])
            access_token = get_access_token(authorization_code)
            logger.info("Successfully authenticated and obtained access token.")
            # Update and store new tokens and timestamps in config
            current_time_iso = datetime.now().isoformat()
            config.update({
                "authorization_token": authorization_code,
                "access_token": access_token,
                "auth_last_updated": current_time_iso,
                "access_last_updated": current_time_iso
            })
            write_config(config)
        except KeyError:
            logger.error("Email or password not configured. Please run 'configure'.")
            raise typer.Exit()
        except requests.HTTPError as e:
            logger.error(f"HTTP Error: {e.response.status_code} {e.response.reason}")
            raise typer.Exit()
    else:
        logger.info("Authorization token is still valid.")

    return True

def api_get(endpoint: str, params: dict = None) -> dict:
    """Make a GET request to the SensorPush API."""
    headers = {"Authorization": f"Bearer {config['access_token']}"}
    try:
        response = session.get(f"{BASE_URL}/{endpoint}", headers=headers, params=params)
        response.raise_for_status()  # Raises an HTTPError for bad responses
        return response.json()
    except requests.HTTPError as e:
        logger.error(f"HTTP Error on GET: {e.response.status_code} {e.response.reason}")
        return {}
    except Exception as e:
        logger.error(f"An unexpected error occurred on GET: {str(e)}")
        return {}

def api_post(endpoint: str, data: dict = None ) -> dict:
    """Make a POST request to the SensorPush API."""
    headers = {
        "Authorization": f"Bearer {config['access_token']}",
        "Content-Type": "application/json"
    }
    try:
        url = f"{BASE_URL}/{endpoint}"
        response = session.post(url, headers=headers, json=data)
        response.raise_for_status()  # Raises an HTTPError for bad responses
        return response.json()
    except requests.HTTPError as e:
        logger.error(f"HTTP Error on POST: {url} {e.response.status_code} {e.response.reason}")
        return {}
    except Exception as e:
        logger.error(f"An unexpected error occurred on POST: {str(e)}")
        return {}

def get_sensordata(startTime: datetime = None, sensors: list = None, stopTime: datetime = None) -> dict:
    """Query SensorPush for samples based on specified time range and sensor list."""
    # Format startTime and endTime to the appropriate string format for the API
    startTime_str = startTime.isoformat() if startTime else None
    stopTime_str = stopTime.isoformat() if stopTime else None
    
    # Prepare the payload for the request
    payload = {
        "sensors": sensors,
    }

    if startTime_str:  # Only add startTime if startTime is specified
        payload["startTime"] = startTime_str

    if stopTime_str:  # Only add stopTime if stopTime is specified
        payload["stopTime"] = stopTime_str
    
    # Use the api_post function to query the /samples endpoint
    response = api_post("samples", payload)
    
    # Check the response and handle errors or return the data
    if not response:
        logger.error("Failed to retrieve sensor data.")
        return {}
    
    return response

def send_zabbix_data(hostname: str, data: dict):
    """Send data to Zabbix using the ZabbixSender class."""
    # Create a list of ZabbixMetric objects
    metrics = []
    for key, value in data.items():
        metrics.append(ZabbixMetric(hostname, key, value))
    
    # Create a ZabbixSender object and send the data.  TODO error checking, config management
    sender = ZabbixSender(config['zabbix_server'])
    try:
        sender.send(metrics)
    except Exception as e:
        logger.error(f"Error sending data to Zabbix: {str(e)}")
        raise typer.Exit()
        
    logger.info(f"Data sent to Zabbix for {hostname}.")

###
### COMMANDS
###

@app.command()
def getzdata():
    """Display most recent sensorpush data for all configured
    sensors and display it in a format suitable for zabbix"""

    # get a list of configured sensors from the config file.  If sensors
    # is undefined or empty, exit with an error message.
    if 'sensors' not in config or not config['sensors']:
        logger.error("No sensors are configured. Please run 'configsensor'.")
        raise typer.Exit()

    # use get_sensordata to get the most recent data for each sensor.  Limit this to 30 minutes of data
    now = datetime.now()
    try:
        #data = get_sensordata(now - timedelta(minutes=30), list(config['sensors'].keys()), now)
        data = get_sensordata(sensors = list(config['sensors'].keys()))
    except ValueError as e:
        logger.error(f"Error getting sensor data: {str(e)}")
        raise typer.Exit()

    # print the data in a format suitable for zabbix.  Iterate over the list of sensors and print the hostname
    # and the most recent sample for each hostname, measurement type, and value.
    for sensorid in data['sensors']:
        # skip if no data is available
        if len(data['sensors'][sensorid]) == 0:
            continue

        hostname = config['sensors'][sensorid]['hostname']
        # the first sample in the list is the most recent
        sample = data['sensors'][sensorid][0]

        # calculate the age of the most recent sample in seconds
        age = (datetime.now(timezone.utc) - parser.isoparse(sample['observed'])).total_seconds()
        logger.info(f"Sample age for {hostname}: {age} seconds {sample['observed']}")

        # skip if the observed value for the most recent reading is more than 5 minutes old.  TODO make this configurable
        if datetime.now(timezone.utc) - parser.isoparse(sample['observed']) > timedelta(minutes=5):
            continue

        # for each of temparature, humidity, pressure, dewpoint, and vpd, print the hostname, measurement type, and value
        zdata = {}
        for measurement in ['temperature', 'humidity', 'pressure', 'dewpoint', 'vpd']:
            if measurement in sample:
                #typer.echo(f"{hostname} sensorpush[{measurement}] {sample[measurement]}")
                logger
                zdata[f"sensorpush[{measurement}]"] = sample[measurement]
        
        # send it
        send_zabbix_data(hostname, zdata)

@app.command()
def configsensor():
    """Configure a SensorPush sensor fpr export to zabbix"""

    # if config['sensors'] doesn't exist, create it
    if 'sensors' not in config:
        config['sensors'] = {}

    # start with a list of all sensors
    # list all sensors. 
    sensors = api_post("devices/sensors", {} )
    sensorlist = []

    # menu header
    typer.echo("Select a sensor to configure:")

    listref = 0
    # gather a list of all sensors and display a sensor list
    for sensorid in sensors:
        sensorname = sensors[sensorid]["name"]
        sensorlist.append(sensorid) # the ID will be used for reference, not the name

        # print the index and sensor name without a line break
        typer.echo(f"{listref}: {sensorid} ({sensorname}) ", nl=False)

        sensor_hostname = None
        # check if this sensor is already configured
        if sensorid in config['sensors']:
            #sensor_hostname = config['sensors'][sensorname]['hostname']
            sensor_hostname = config['sensors'][sensorid]['hostname']
            typer.echo(f" - Configured as {sensor_hostname}")
        else:
            typer.echo(" - Not configured")
        # increment ihdex
        listref += 1
    
    # prompt for a sensor to configure
    valid = False
    while not valid:
        sensor_index = typer.prompt("Select a sensor to configure", type=int)

        # validate input
        if sensor_index < 0 or sensor_index >= len(sensorlist):
            typer.echo("Invalid sensor index. Please try again.")
        else:
            valid = True
    
    # prompt for a hostname.
    valid = False
    while not valid:
        defname = sensors[sensorlist[sensor_index]]["name"]
        hostname = typer.prompt("Enter a hostname for the sensor", default=defname)
        if hostname == "":
            typer.echo("Hostname cannot be empty. Please try again.")
        else:
            valid = True
    
    # update the config with the new sensor hostname
    sensorid = sensorlist[sensor_index]
    config['sensors'][sensorid] = {'hostname': hostname}

    # save the config
    write_config(config)

    logger.info(f"Sensor {sensorid} configured as {hostname}")

@app.command()
def status():
    """Check the status of the SensorPush service."""
    #global config
    #config = load_config()
    
    # authenticate to SensorPush
    if not authenticate():
        logger.error("Authentication failed. Check configuration or service availability'.")
        raise typer.Exit()

    # Get the status of the SensorPush service
    statdata = api_post("")
    typer.echo(f"API Status: {statdata['status']}")
    logger.debug(f"API Status: {statdata}")

    # list all gateways and collect the last_seen attribute for each. Set status to:
    # OK if it last_seen was within the last minute
    # WARNING if it 
    # CRITICAL if it was more than 5 minutes ago
    gateways = api_post("devices/gateways", {})

    typer.echo("\nGateways:")
    for gwname in gateways:
        gw = gateways[gwname]
        
        last_seen = parser.isoparse(gw["last_seen"])
        if datetime.now(timezone.utc) - last_seen.replace(tzinfo=timezone.utc) < timedelta(minutes=5):
            status = "OK"
        elif datetime.now(timezone.utc) - last_seen.replace(tzinfo=timezone.utc) < timedelta(minutes=10):
            status = "WARNING"
        else:
            status = "CRITICAL"
        typer.echo(f"{gwname} - status: {status} (last seen: {last_seen})")

    # list all sensors. 
    sensors = api_post("devices/sensors", {} )
    typer.echo("\nSensors:")
    for sensorid in sensors:
        sensor = sensors[sensorid]
        sensorname = sensor["name"]
        rssi = sensor["rssi"]
        type = sensor["type"]
        battery_voltage = sensor["battery_voltage"]

        typer.echo(f"{sensorid} - {sensorname} - RSSI: {rssi}, Type: {type}, Battery: {battery_voltage}V")


@app.command()
def configure():
    #config = load_config()
    email = typer.prompt("Email", default=config.get("email", ""))
    password = getpass("Password: ")
    
    # Update the config dictionary with new values
    config.update({"email": email, "password": password})
    write_config(config)

@app.callback()
def main():
    # Set up logging
    setup_logging()

    # Ensure the .spaz directory exists
    if not config_path.parent.exists():
        try:
            config_path.parent.mkdir(parents=True)
        except PermissionError:
            logger.error(f"Could not create the directory {config_path.parent}. Please check permissions.")
            raise typer.Exit()

    # Load the configuration
    global config
    config = load_config()
    logger.info("Spaz started.")

    # create a session to use for all requests
    global session
    session = requests.Session()

if __name__ == "__main__":
    app()

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
#config_file = Path.home() / ".spaz" / "config.json"
#log_path = Path.home() / ".spaz" / "log"
config_file = ""
log_path = ""

# query count for debugging
query_count = 0

# set up logging
def setup_logging():
    # Remove any existing handlers
    logger.remove()
    
    # Add a handler for errors only to stderr
    logger.add(sys.stderr, level="ERROR", format="{time} {level} {message}")

    # Log everything to a file called log in log_path
    log_file = log_path / "log"
    logger.add(log_file, level="DEBUG", format="{time} {level} {message}")

def load_config():
    if Path(config_file).exists():
        try:
            with open(config_file, "r") as file:
                return json.load(file)
        except json.JSONDecodeError:
            logger.error("Error decoding config file. Ensure it is valid JSON.")
            return {}
    else:
        return {}

def write_config(config):
    config_file.parent.mkdir(parents=True, exist_ok=True)
    with open(config_file, "w") as file:
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
    return datetime.now(timezone.utc) - last_updated < timedelta(seconds=validity_duration)

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
            current_time_iso = datetime.now(timezone.utc).isoformat()
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
        global query_count
        query_count += 1
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
        global query_count
        query_count += 1
        return response.json()
    except requests.HTTPError as e:
        logger.error(f"HTTP Error on POST: {url} {e.response.status_code} {e.response.reason}")
        return {}
    except Exception as e:
        logger.error(f"An unexpected error occurred on POST: {str(e)}")
        return {}

def get_sensordata(startTime: datetime = None, sensors: list = None, stopTime: datetime = None, limit: int = None) -> dict:
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
    
    if limit:  # Only add limit if limit is specified
        payload["limit"] = limit

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
    for key, dat in data.items():
        for item in dat:
            value = item['value']
            # make sure the converted value is in unix timestamp relative to utc
            clocktime = int(parser.isoparse(item['observed']).timestamp())
            logger.debug(f"Sending Zabbix data for {hostname}: {key} {value} at {clocktime}")
            metrics.append(ZabbixMetric(hostname, key, value, clock=clocktime))
    
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
def getzdata(ctx: typer.Context):
    """Display most recent sensorpush data for all configured
    sensors and display it in a format suitable for zabbix"""

    # record the time the command was STARTED, so we can store it in the config if successful.
    # we do this early so subsequent calls don't miss any data.  This subcommand could take a while to run
    start_time = datetime.now(timezone.utc).isoformat()

    # reas and parse the last_run from the config if available
    # TODO this probably good enough for most use cases, but if we really want to be
    # accurate we should do this per sensor.
    last_run = None
    if 'last_run' in config:
        last_run = parser.isoparse(config['last_run'])

    # authenticate to SensorPush
    if not authenticate():
        logger.error("Authentication failed. Check configuration or service availability'.")
        raise typer.Exit()

    # get a list of configured sensors from the config file.  If sensors
    # is undefined or empty, exit with an error message.
    if 'sensors' not in config or not config['sensors']:
        logger.error("No sensors are configured. Please run 'configsensor'.")
        raise typer.Exit()

    # get the metadata for all sensors, too.  This will be used for battery voltage, rssi, etc.
    sensormeta = api_post("devices/sensors", {} )

    # use get_sensordata to get the most recent data for each sensor.  Limit this to 30 minutes of data
    now = datetime.now(timezone.utc)
    try:
        #data = get_sensordata(now - timedelta(minutes=30), list(config['sensors'].keys()), now)
        #data = get_sensordata(sensors = list(config['sensors'].keys()))
        logger.debug(f"Getting sensor data for sensors: {list(config['sensors'].keys())} from {last_run} to now")
        data = get_sensordata(sensors = list(config['sensors'].keys()), stopTime = last_run, limit=1440)
    except ValueError as e:
        logger.error(f"Error getting sensor data: {str(e)}")
        raise typer.Exit()

    # record whether or not data was sent to zabbix
    data_sent = False

    # print the data in a format suitable for zabbix.  Iterate over the list of sensors and print the hostname
    # and the most recent sample for each hostname, measurement type, and value.
    for sensorid in data['sensors']:
        # skip if no data is available
        if len(data['sensors'][sensorid]) == 0:
            continue

        # if the timestamp of the most recent sample is not newer than the last sample, skip it
        if 'last_sample' in config['sensors'][sensorid]:
            if parser.isoparse(data['sensors'][sensorid][0]['observed']) <= parser.isoparse(config['sensors'][sensorid]['last_sample']):
                continue

        hostname = config['sensors'][sensorid]['hostname']
        zdata = {}
        
        # iterate over the samples for this sensor
        for sample in data['sensors'][sensorid]:
            # the first sample in the list is the most recent
            #sample = data['sensors'][sensorid][0]

            typer.echo(f"Sample for {hostname}: {sample['observed']}")
            # calculate the age of the most recent sample in seconds
            age = (datetime.now(timezone.utc) - parser.isoparse(sample['observed'])).total_seconds()
            logger.info(f"Sample age for {hostname}: {age} seconds {sample['observed']}")

            # skip if the observed value for the most recent reading is more than 5 minutes old.  TODO make this configurable
            #if datetime.now(timezone.utc) - parser.isoparse(sample['observed']) > timedelta(minutes=5):
            #    continue

            for measurement in ['temperature', 'humidity', 'pressure', 'dewpoint', 'vpd']:
                if measurement in sample:
                    #typer.echo(f"{hostname} sensorpush[{measurement}] {sample[measurement]}")
                    logger.info(f"Sending zabbix data for {hostname}: sensorpush[{measurement}] {sample[measurement]}")
                    # zdata is a dictionary of arrays.  Each array contains a dictionary with observed and value keys
                    # add observed and value to the array for each measurement
                    zdata.setdefault(f"sensorpush[{measurement}]", []).append({
                        'observed': sample['observed'],
                        'value': sample[measurement]
                    })
            
        # add metadata for this sensor to the zabbix data  This is only run once per sensor per query.
        for measurement in ['rssi', 'battery_voltage']:
            if measurement in sensormeta[sensorid]:
                #typer.echo(f"{hostname} sensorpush[{measurement}] {sensormeta[sensorid][measurement]}")
                logger.info(f"Sending zabbix data for {hostname}: sensorpush[{measurement}] {sensormeta[sensorid][measurement]}")
                zdata.setdefault(f"sensorpush[{measurement}]", []).append({
                    'observed': sample['observed'],
                    'value': sensormeta[sensorid][measurement]
                })

        # send it
        if ctx.obj["DRY_RUN"]:
            typer.echo(f"DRY RUN: Would send zabbix data for {hostname}: {len(zdata)} samples")
            logger.info(f"DRY RUN: Would send zabbix data for {hostname}: {len(zdata)} samples")
        else:
            send_zabbix_data(hostname, zdata)
            data_sent = True
            # store the time of the most recent sample in the config file
            config['sensors'][sensorid]['last_sample'] = sample['observed']

    # if data was sent, save the config
    if data_sent:
        # save the config
        config['last_run'] = start_time
        write_config(config)

    # debug.  print the number of queries made to the sensorpush api
    logger.debug(f"Queries made: {query_count}")
    #typer.echo(f"Queries made: {query_count}")
    
@app.command()
def configsensor(ctx: typer.Context):
    """Configure a SensorPush sensor fpr export to zabbix"""

    # authenticate to SensorPush
    if not authenticate():
        logger.error("Authentication failed. Check configuration or service availability'.")
        raise typer.Exit()

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
def status(ctx: typer.Context):
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
def configure(ctx: typer.Context):
    #config = load_config()
    
    # TODO lots of error checking needed

    email = typer.prompt("Email", default=config.get("email", ""))
    password = getpass("Password: ")
    zabbix_server = typer.prompt("Zabbix server", default=config.get("zabbix_server", ""))
    
    # Update the config dictionary with new values
    config.update({"email": email, "password": password})
    write_config(config)

@app.callback()
def main(
    ctx: typer.Context,
    cf: str = typer.Option(
        os.path.join(Path.home(), ".spaz/config.json"),
        "--config",
        "-c",
        help="Path to the configuration file."
    ),
    log_dir: str = typer.Option(
        os.path.join(Path.home(), ".spaz/"),
        "--logdir",
        help="Directory to store log files."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Perform a dry run without making any changes."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress all non-error output."),
    force: bool = typer.Option(False, "--force", "-f", help="Force update even if not recommended.")
):
    ctx.ensure_object(dict)

    # TODO you have broken logging so badly that it's not even funny.  Fix it.
    global log_path
    log_path = Path(log_dir)

    # Set up logging
    setup_logging()

    # Ensure the .spaz directory exists
    global config_file
    config_file = Path(cf)
    if not config_file.parent.exists():
        try:
            cfpath.parent.mkdir(parents=True)
        except PermissionError:
            logger.error(f"Could not create the directory {cfpath.parent}. Please check permissions.")
            raise typer.Exit()

    # Load the configuration
    global config
    config = load_config()
    
    # also store the config in the context object
    ctx.obj["CONFIG"] = config

    logger.info("Spaz started.")

    # create a session to use for all requests
    global session
    session = requests.Session()

    # record remaining options in the context object
    ctx.obj["LOG_DIR"] = Path(log_path)
    ctx.obj["DRY_RUN"] = dry_run
    ctx.obj["QUIET"] = quiet
    ctx.obj["FORCE"] = force

app.command()(configure)
app.command()(status)
app.command()(getzdata)
app.command()(configsensor)



if __name__ == "__main__":
    app()

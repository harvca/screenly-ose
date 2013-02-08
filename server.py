#!/usr/bin/env python
# -*- coding: utf8 -*-

__author__ = "Viktor Petersson"
__copyright__ = "Copyright 2012, WireLoad Inc"
__license__ = "Dual License: GPLv2 and Commercial License"
__version__ = "0.1.2"
__email__ = "vpetersson@wireload.net"

from datetime import datetime, timedelta
from dateutils import datestring
from hashlib import md5
from hurry.filesize import size
from netifaces import ifaddresses
from os import path, makedirs, getloadavg, statvfs, mkdir, remove as remove_file
from PIL import Image
from requests import get as req_get, head as req_head
from StringIO import StringIO
from subprocess import check_output
from urlparse import urlparse
import json
from uptime import uptime

from bottle import route, run, template, request, error, static_file, response, redirect
from bottlehaml import haml_template

from db import connection
from utils import json_dump

from settings import get_current_time, asset_folder
import settings


################################
# Utilities
################################

def is_active(asset, at_time=None):
    """Accepts an asset dictionary and determines if it
    is active at the given time. If no time is specified,
    get_current_time() is used.

    >>> asset = {'asset_id': u'4c8dbce552edb5812d3a866cfe5f159d', 'mimetype': u'web', 'name': u'WireLoad', 'end_date': datetime(2013, 1, 19, 23, 59), 'uri': u'http://www.wireload.net', 'duration': u'5', 'start_date': datetime(2013, 1, 16, 0, 0)};

    >>> is_active(asset, datetime(2013, 1, 16, 12, 00))
    True
    >>> is_active(asset, datetime(2014, 1, 1))
    False

    """

    if not (asset['start_date'] and asset['end_date']):
        return False

    at_time = at_time or get_current_time()

    return (asset['start_date'] < at_time and asset['end_date'] > at_time)


def get_playlist():
    playlist = []
    for asset in fetch_assets():
        if is_active(asset):
            asset['start_date'] = datestring.date_to_string(asset['start_date'])
            asset['end_date'] = datestring.date_to_string(asset['end_date'])

            playlist.append(asset)

    return playlist


def fetch_assets(keys=None, order_by="name"):
    """Fetches all assets from the database and returns their
    data as a list of dictionaries corresponding to each asset."""
    c = connection.cursor()

    if keys is None:
        keys = [
            "asset_id", "name", "uri", "start_date",
            "end_date", "duration", "mimetype"
        ]

    c.execute("SELECT %s FROM assets ORDER BY %s" % (", ".join(keys), order_by))
    assets = []

    for asset in c.fetchall():
        dictionary = {}
        for i in range(len(keys)):
            dictionary[keys[i]] = asset[i]
        assets.append(dictionary)

    return assets


def get_assets_grouped():
    """Returns a dictionary containing a list of active assets
    and a list of inactive assets stored at their respective
    keys. Example: {'active': [...], 'inactive': [...]}"""

    assets = fetch_assets()
    active = []
    inactive = []

    for asset in assets:
        if is_active(asset):
            active.append(asset)
        else:
            inactive.append(asset)

    return {'active': active, 'inactive': inactive}


def initiate_db():
    # Create config dir if it doesn't exist
    if not path.isdir(settings.configdir):
        makedirs(settings.configdir)

    c = connection.cursor()

    # Check if the asset-table exist. If it doesn't, create it.
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assets'")
    asset_table = c.fetchone()

    if not asset_table:
        c.execute("CREATE TABLE assets (asset_id TEXT, name TEXT, uri TEXT, md5 TEXT, start_date TIMESTAMP, end_date TIMESTAMP, duration TEXT, mimetype TEXT)")
        return "Initiated database."


def validate_uri(uri):
    """Simple URL verification.

    >>> validate_uri("hello")
    False
    >>> validate_uri("ftp://example.com")
    False
    >>> validate_uri("http://")
    False
    >>> validate_uri("http://wireload.net/logo.png")
    True
    >>> validate_uri("https://wireload.net/logo.png")
    True

    """

    uri_check = urlparse(uri)

    return bool(uri_check.scheme in ('http', 'https') and uri_check.netloc)


def make_json_response(obj):
    response.content_type = "application/json"
    return json_dump(obj)


def api_error(error):
    response.content_type = "application/json"
    response.status = 500
    return json_dump({'error': error})


################################
# API
################################

def prepare_asset(request):

    data = request.POST or request.FORM or {}

    if 'model' in data:
        data = json.loads(data['model'])

    def get(key):
        return data.get(key, '').strip()

    if all([
        get('name'),
        get('uri') or (request.files.file_upload != ""),
        get('mimetype')]):

        asset = {
            'name': get('name').decode('UTF-8'),
            'mimetype': get('mimetype'),
        }

        uri = get('uri') or False

        try:
            file_upload = request.files.file_upload.file
        except:
            file_upload = False

        if file_upload and 'web' in asset['mimetype']:
            raise Exception("Invalid combination. Can't upload a web resource.")

        if uri and file_upload:
            raise Exception("Invalid combination. Can't select both URI and a file.")

        if uri:
            if not validate_uri(uri):
                raise Exception("Invalid URL. Failed to add asset.")

            if "image" in asset['mimetype']:
                file = req_get(uri, allow_redirects=True)
            else:
                file = req_head(uri, allow_redirects=True)

            if file.status_code == 200:
                asset['asset_id'] = md5(asset['name'] + uri).hexdigest()
                asset['uri'] = uri
                # strict_uri = file.url

                # if "image" in asset['mimetype']:
                #     asset['resolution'] = Image.open(StringIO(file.content)).size

            else:
                raise Exception("Could not retrieve file. Check the asset URL.")

        if file_upload:
            asset['asset_id'] = md5(file_upload.read()).hexdigest()
            asset['uri'] = path.join(asset_folder, asset['asset_id'])

            with open(asset['uri'], 'w') as f:
                f.write(file_upload.read())

        # if not asset.get('resolution', False):
        #     asset['resolution'] = "N/A"

        if "video" in asset['mimetype']:
            asset['duration'] = "N/A"

        if get('duration'):
            asset['duration'] = get('duration')

        if get('start_date'):
            asset['start_date'] = datetime.strptime(get('start_date').split(".")[0], "%Y-%m-%dT%H:%M:%S")
        else:
            asset['start_date'] = ""

        if get('end_date'):
            asset['end_date'] = datetime.strptime(get('end_date').split(".")[0], "%Y-%m-%dT%H:%M:%S")
        else:
            asset['end_date'] = ""

        return asset
    else:
        raise Exception("Not enough information provided. Please specify 'name', 'uri', and 'mimetype'.")


@route('/api/assets', method="GET")
def api_assets():

    assets = fetch_assets()

    for asset in assets:
        asset['is_active'] = is_active(asset)

    return make_json_response(assets)


@route('/api/assets', method="POST")
def add_asset():
    try:
        asset = prepare_asset(request)

        c = connection.cursor()
        c.execute(
            "INSERT INTO assets (%s) VALUES (%s)" % (", ".join(asset.keys()), ",".join(["?"] * len(asset.keys()))),
            asset.values()
        )
        connection.commit()
    except Exception as e:
        return api_error(str(e))

    redirect("/")


@route('/api/assets/:asset_id', method="POST")
def edit_asset(asset_id):
    try:
        asset = prepare_asset(request)
        del asset['asset_id']

        c = connection.cursor()
        query = "UPDATE assets SET %s=? WHERE asset_id=?" % "=?, ".join(asset.keys())

        c.execute(query, asset.values() + [asset_id])
        connection.commit()
    except Exception as e:
        return api_error(str(e))

    redirect("/")


################################
# Views
################################

@route('/')
def viewIndex():
    initiate_db()
    assets = get_assets_grouped()
    return haml_template('index', assets=assets)


@route('/settings')
def settings_page():
    return haml_template('settings')


@route('/process_asset', method='POST')
def process_asset():
    c = connection.cursor()

    if (request.POST.get('name', '').strip() and
        (request.POST.get('uri', '').strip() or request.files.file_upload.file) and
        request.POST.get('mimetype', '').strip()
        ):

        name = request.POST.get('name', '').decode('UTF-8')
        mimetype = request.POST.get('mimetype', '').strip()

        try:
            uri = request.POST.get('uri', '').strip()
        except:
            uri = False

        try:
            file_upload = request.files.file_upload.file
        except:
            file_upload = False

        # Make sure it is a valid combination
        if (file_upload and 'web' in mimetype):
            header = "Ops!"
            message = "Invalid combination. Can't upload web resource."
            return template('message', header=header, message=message)

        if (uri and file_upload):
            header = "Ops!"
            message = "Invalid combination. Can't select both URI and a file."
            return template('message', header=header, message=message)

        if uri:
            if not validate_uri(uri):
                header = "Ops!"
                message = "Invalid URL. Failed to add asset."
                return template('message', header=header, message=message)

            if "image" in mimetype:
                file = req_get(uri, allow_redirects=True)
            else:
                file = req_head(uri, allow_redirects=True)

            # Only proceed if fetch was successful.
            if file.status_code == 200:
                asset_id = md5(name + uri).hexdigest()

                strict_uri = file.url

                if "image" in mimetype:
                    resolution = Image.open(StringIO(file.content)).size
                else:
                    resolution = "N/A"

                if "video" in mimetype:
                    duration = "N/A"
            else:
                header = "Ops!"
                message = "Unable to fetch file."
                return template('message', header=header, message=message)

        if file_upload:
            asset_id = md5(file_upload.read()).hexdigest()

            local_uri = path.join(asset_folder, asset_id)
            f = open(local_uri, 'w')
            asset_file_input = file_upload.read()
            f.write(asset_file_input)
            f.close()

            uri = local_uri

        start_date = ""
        end_date = ""
        duration = ""

        c.execute("INSERT INTO assets (asset_id, name, uri, start_date, end_date, duration, mimetype) VALUES (?,?,?,?,?,?,?)", (asset_id, name, uri, start_date, end_date, duration, mimetype))
        connection.commit()

        header = "Yay!"
        message = "Added asset (" + asset_id + ") to the database."
        return template('message', header=header, message=message)

    else:
        header = "Ops!"
        message = "Invalid input."
        return template('message', header=header, message=message)


@route('/process_schedule', method='POST')
def process_schedule():
    c = connection.cursor()

    if (request.POST.get('asset', '').strip() and
        request.POST.get('start', '').strip() and
        request.POST.get('end', '').strip()
        ):

        asset_id = request.POST.get('asset', '').strip()
        input_start = request.POST.get('start', '').strip()
        input_end = request.POST.get('end', '').strip()

        start_date = datetime.strptime(input_start, '%Y-%m-%d @ %H:%M')
        end_date = datetime.strptime(input_end, '%Y-%m-%d @ %H:%M')

        query = c.execute("SELECT mimetype FROM assets WHERE asset_id=?", (asset_id,))
        asset_mimetype = c.fetchone()

        if "image" or "web" in asset_mimetype:
            try:
                duration = request.POST.get('duration', '').strip()
            except:
                header = "Ops!"
                message = "Duration missing. This is required for images and web-pages."
                return template('message', header=header, message=message)
        else:
            duration = "N/A"

        c.execute("UPDATE assets SET start_date=?, end_date=?, duration=? WHERE asset_id=?", (start_date, end_date, duration, asset_id))
        connection.commit()

        header = "Yes!"
        message = "Successfully scheduled asset."
        return template('message', header=header, message=message)

    else:
        header = "Ops!"
        message = "Failed to process schedule."
        return template('message', header=header, message=message)


@route('/update_asset', method='POST')
def update_asset():
    c = connection.cursor()

    if (request.POST.get('asset_id', '').strip() and
        request.POST.get('name', '').strip() and
        request.POST.get('uri', '').strip() and
        request.POST.get('mimetype', '').strip()
        ):

        asset_id = request.POST.get('asset_id', '').strip()
        name = request.POST.get('name', '').decode('UTF-8')
        uri = request.POST.get('uri', '').strip()
        mimetype = request.POST.get('mimetype', '').strip()

        if not validate_uri(uri) and asset_folder not in uri:
            header = "Ops!"
            message = "Invalid URL. Failed to update asset."
            return template('message', header=header, message=message)

        try:
            duration = request.POST.get('duration', '').strip()
        except:
            duration = None

        try:
            input_start = request.POST.get('start', '')
            start_date = datetime.strptime(input_start, '%Y-%m-%d @ %H:%M')
        except:
            start_date = None

        try:
            input_end = request.POST.get('end', '').strip()
            end_date = datetime.strptime(input_end, '%Y-%m-%d @ %H:%M')
        except:
            end_date = None

        c.execute("UPDATE assets SET start_date=?, end_date=?, duration=?, name=?, uri=?, duration=?, mimetype=? WHERE asset_id=?", (start_date, end_date, duration, name, uri, duration, mimetype, asset_id))
        connection.commit()

        header = "Yes!"
        message = "Successfully updated asset."
        return template('message', header=header, message=message)

    else:
        header = "Ops!"
        message = "Failed to update asset."
        return template('message', header=header, message=message)


@route('/delete_asset/:asset_id')
def delete_asset(asset_id):
    c = connection.cursor()

    c.execute("DELETE FROM assets WHERE asset_id=?", (asset_id,))
    try:
        connection.commit()

        # If file exist on disk, delete it.
        local_uri = path.join(asset_folder, asset_id)
        if path.isfile(local_uri):
            remove_file(local_uri)

        header = "Success!"
        message = "Deleted asset."
        return template('message', header=header, message=message)
    except:
        header = "Ops!"
        message = "Failed to delete asset."
        return template('message', header=header, message=message)


@route('/system_info')
def system_info():
    viewer_log_file = '/tmp/screenly_viewer.log'
    if path.exists(viewer_log_file):
        viewlog = check_output(['tail', '-n', '20', viewer_log_file]).split('\n')
    else:
        viewlog = ["(no viewer log present -- is only the screenly server running?)\n"]

    # Get load average from last 15 minutes and round to two digits.
    loadavg = round(getloadavg()[2],2)

    try:
        resolution = check_output(['tvservice', '-s']).strip()
    except:
        resolution = None

    # Calculate disk space
    slash = statvfs("/")
    free_space = size(slash.f_bavail * slash.f_frsize)

    # Get uptime
    uptime_in_seconds = uptime()
    system_uptime = timedelta(seconds=uptime_in_seconds)

    return haml_template('system_info', viewlog=viewlog, loadavg=loadavg, free_space=free_space, uptime=system_uptime, resolution=resolution)


@route('/splash_page')
def splash_page():
    # Make sure the database exist and that it is initiated.
    initiate_db()

    try:
        my_ip = ifaddresses('eth0')[2][0]['addr']
        ip_lookup = True
        url = 'http://' + my_ip + ':8080'
    except:
        ip_lookup = False
        url = "Unable to lookup IP from eth0."

    return template('splash_page', ip_lookup=ip_lookup, url=url)


@route('/view_playlist')
def view_node_playlist():
    nodeplaylist = get_playlist()

    return template('view_playlist', nodeplaylist=nodeplaylist)


@route('/view_assets')
def view_assets():
    nodeplaylist = fetch_assets()

    return template('view_assets', nodeplaylist=nodeplaylist)


@route('/add_asset')
def add_asset():
    return template('add_asset')


@route('/schedule_asset')
def schedule_asset():
    c = connection.cursor()

    assets = []
    c.execute("SELECT name, asset_id FROM assets ORDER BY name")
    query = c.fetchall()
    for asset in query:
        name = asset[0]
        asset_id = asset[1]

        assets.append({
            'name': name,
            'asset_id': asset_id,
        })

    return template('schedule_asset', assets=assets)


@route('/edit_asset/:asset_id')
def edit_asset(asset_id):
    c = connection.cursor()

    c.execute("SELECT name, uri, md5, start_date, end_date, duration, mimetype FROM assets WHERE asset_id=?", (asset_id,))
    asset = c.fetchone()

    name = asset[0]
    uri = asset[1]
    md5 = asset[2]

    if asset[3]:
        start_date = datestring.date_to_string(asset[3])
    else:
        start_date = None

    if asset[4]:
        end_date = datestring.date_to_string(asset[4])
    else:
        end_date = None

    duration = asset[5]
    mimetype = asset[6]

    asset_info = {
            "name": name,
            "uri": uri,
            "duration": duration,
            "mimetype": mimetype,
            "asset_id": asset_id,
            "start_date": start_date,
            "end_date": end_date
            }
    #return str(asset_info)
    return template('edit_asset', asset_info=asset_info)


@error(403)
def mistake403(code):
    return 'The parameter you passed has the wrong format!'


@error(404)
def mistake404(code):
    return 'Sorry, this page does not exist!'


################################
# Static
################################

@route('/static/:path#.+#', name='static')
def static(path):
    return static_file(path, root='static')


if __name__ == "__main__":
    # Make sure the asset folder exist. If not, create it
    if not path.isdir(asset_folder):
        mkdir(asset_folder)

    run(host=settings.listen_ip, port=settings.listen_port, reloader=True)

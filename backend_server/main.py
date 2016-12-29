"""Flask Backend Server for managing audio content.

Please see README.md for instructions.

Starting Locally
----------------
You have two options:

  $ python main.py --port 8080 --local --debug

Or, to use GCP backend by default:

  $ dev_appserver.py .


Endpoints
---------
  - /audio : POST
  - /audio/<uri> : GET
  - /annotation/submit : POST
  - /annotation/taxonomy : GET
"""

import argparse
import datetime
from flask import Flask, request, Response
from flask import send_file
from flask_cors import CORS
import io
import json
import logging
import mimetypes
import random
import requests
import os

import pybackend.database
import pybackend.storage
import pybackend.utils


logging.basicConfig(level=logging.DEBUG)
app = Flask(__name__)

# TODO: One of the following
#  - Whitelist localhost and `SOURCE` below.
#  - Use AppEngine for delivery of the annotator HTML.
CORS(app)

# Set the cloud backend
# TODO: This should be controlled by `app.yaml`, right?
CLOUD_CONFIG = os.path.join(os.path.dirname(__file__), 'gcloud_config.json')
app.config['cloud'] = json.load(open(CLOUD_CONFIG))

SOURCE = "https://cosmir.github.io/open-mic/"
AUDIO_EXTENSIONS = set(['wav', 'ogg', 'mp3', 'au', 'aiff'])


@app.route('/api/v0.1/audio', methods=['POST'])
def audio_upload():
    """
    To POST files to this endpoint:

    $ curl -F "audio=@some_file.mp3" localhost:8080/api/v0.1/audio

    TODOs:
      - Store user data (who uploaded this? IP address?)
      - File metadata
    """
    app.logger.info("Upload request from {}".format(request.remote_addr))

    audio_data = request.files['audio']
    file_ext = os.path.splitext(audio_data.filename)[-1][1:]
    if file_ext not in AUDIO_EXTENSIONS:
        logging.exception('Attempted upload of unsupported filetype.')
        return 'Filetype not supported.', 400

    bytestring = audio_data.stream.read()

    # Copy to cloud storage
    store = pybackend.storage.Storage(
        project_id=app.config['cloud']['project_id'],
        **app.config['cloud']['storage'])

    uri = str(pybackend.utils.uuid(bytestring))
    filepath = os.path.extsep.join([uri, file_ext])
    store.upload(bytestring, filepath)

    # Index in the database
    dbase = pybackend.database.Database(
        project_id=app.config['cloud']['project_id'],
        **app.config['cloud']['audio-db'])
    record = dict(filepath=filepath,
                  created=str(datetime.datetime.now()),
                  remote_addr=request.remote_addr,
                  num_bytes=len(bytestring),
                  **request.form)
    dbase.put(uri, record)
    response_data = dict(
        uri=uri,
        message="Received {} bytes of data.".format(len(bytestring)))

    resp = Response(json.dumps(response_data), status=200,
                    mimetype=mimetypes.types_map[".json"])
    resp.headers['Link'] = SOURCE
    return resp


@app.route('/api/v0.1/audio/<uri>', methods=['GET'])
def audio_download(uri):
    """
    To GET responses from this endpoint:

    $ curl -XGET localhost:8080/audio/bbdde322-c604-4753-b828-9fe8addf17b9
    """

    dbase = pybackend.database.Database(
        project_id=app.config['cloud']['project_id'],
        **app.config['cloud']['audio-db'])

    entity = dbase.get(uri)
    if entity is None:
        msg = "Resource not found: {}".format(uri)
        app.logger.info(msg)
        resp = Response(
            json.dumps(dict(message=msg)),
            status=404)

    else:
        store = pybackend.storage.Storage(
            project_id=app.config['cloud']['project_id'],
            **app.config['cloud']['storage'])

        data = store.download(entity['filepath'])
        app.logger.debug("Returning {} bytes".format(len(data)))

        resp = send_file(
            io.BytesIO(data),
            attachment_filename=entity['filepath'],
            mimetype=pybackend.utils.mimetype_for_file(entity['filepath']))

    resp.headers['Link'] = SOURCE
    return resp


@app.route('/api/v0.1/annotation/submit', methods=['POST'])
def annotation_submit():
    """
    To POST data to this endpoint:

    $ curl -H "Content-type: application/json" \
        -X POST localhost:8080/annotation/submit \
        -d '{"message":"Hello Data"}'
    """
    if request.headers['Content-Type'] == 'application/json':
        app.logger.info("Received Annotation:\n{}"
                        .format(json.dumps(request.json, indent=2)))
        # Do a thing with the annotation
        # Return some progress stats?
        data = json.dumps(dict(message='Success!'))
        status = 200

        db = pybackend.database.Database(
            project_id=app.config['cloud']['project_id'],
            **app.config['cloud']['annotation-db'])
        uri = str(pybackend.utils.uuid(json.dumps(request.json)))
        record = dict(created=str(datetime.datetime.now()), **request.json)
        db.put(uri, record)
    else:
        status = 400
        data = json.dumps(dict(message='Invalid Content-Type; '
                                       'only accepts application/json'))

    resp = Response(
        data, status=status, mimetype=mimetypes.types_map[".json"])
    resp.headers['Link'] = SOURCE
    return resp


def get_taxonomy():
    tax_url = ("https://raw.githubusercontent.com/cosmir/open-mic/"
               "ejh_20161119_iss8_webannot/data/instrument_taxonomy_v0.json")
    res = requests.get(tax_url)
    values = []
    try:
        schema = res.json()
        values = schema['tag_open_mic_instruments']['value']['enum']
    except BaseException as derp:
        app.logger.error("Failed loading taxonomy: {}".format(derp))

    return values


@app.route('/api/v0.1/annotation/taxonomy', methods=['GET'])
def annotation_taxonomy():
    """
    To fetch data at this endpoint:

    $ curl -X GET localhost:8080/annotation/taxonomy
    """
    instruments = get_taxonomy()
    status = 200 if instruments else 400

    resp = Response(json.dumps(instruments), status=status)
    resp.headers['Link'] = SOURCE
    return resp


@app.route('/api/v0.1/task', methods=['GET'])
def next_task():
    """
    To fetch data at this endpoint:

    $ curl -X GET localhost:8080/task
    """
    audio_url = "http://localhost:8080/api/v0.1/audio/{}"

    db = pybackend.database.Database(
        project_id=app.config['cloud']['project_id'],
        **app.config['cloud']['audio-db'])

    random_uri = random.choice(list(db.keys()))

    task = dict(feedback="none",
                visualization=random.choice(['waveform', 'spectrogram']),
                proximityTag=[],
                annotationTag=get_taxonomy(),
                url=audio_url.format(random_uri),
                numRecordings='?',
                recordingIndex=random_uri,
                tutorialVideoURL="https://www.youtube.com/embed/Bg8-83heFRM",
                alwaysShowTags=True)
    data = json.dumps(dict(task=task))
    app.logger.debug("Returning:\n{}".format(data))
    resp = Response(data)
    resp.headers['Link'] = SOURCE
    return resp


@app.errorhandler(500)
def server_error(e):
    logging.exception('An error occurred during a request.')
    return 'An internal error occurred.', 500


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port", type=int, default=8080,
        help="Port on which to serve.")
    parser.add_argument(
        "--local",
        action='store_true', help="Use local backend services.")
    parser.add_argument(
        "--debug",
        action='store_true',
        help="Run the Flask application in debug mode.")

    args = parser.parse_args()

    if args.local:
        config = os.path.join(os.path.dirname(__file__), 'local_config.json')
        app.config['cloud'] = json.load(open(config))

    app.run(debug=args.debug, port=args.port)

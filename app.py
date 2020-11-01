#!/usr/bin/env python3

import json
import os.path
import datetime
import redis

from flask import Flask, jsonify, send_from_directory
app = Flask(__name__, static_url_path='', static_folder='')
r = redis.Redis(decode_responses=True)

@app.route('/')
def root():
    return app.send_static_file('index.html')

@app.route('/stream')
def get_stream():
    key = r.randomkey()
    if not key:
        return "{}"

    time_fetched = r.get(key)
    ttl = r.ttl(key)

    response = json.loads(key)
    response['fetched'] = time_fetched
    response['ttl'] = ttl
    return response

if __name__ == "__main__":
    app.run()
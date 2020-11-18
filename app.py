#!/usr/bin/env python3

import json
import os.path
import datetime
from collections import defaultdict, deque
from functools import partial
from random import choice

import redis
from flask import Flask, jsonify, send_from_directory, request

app = Flask(__name__, static_url_path='', static_folder='static')
main_redis = redis.Redis(decode_responses=True, db=0)
stats_redis = redis.Redis(decode_responses=True, db=1)
game_redis = redis.Redis(decode_responses=True, db=2)

# use a defaultdict to make initiating new games easier
# this will be populated by the normal getStreams() calls from fetchAndRenderRandomStream()
# the maxlen will limit the number of streams saved under each game_id.
# todo should a set be used to prevent duplicates? if so, then the size of games_with_streams
#  may get out of hand without maxlen. If using a deque, size won't be an issue, but duplicates
#  will be. I think that deque is the better choice as size appears to be the bigger issue.
#  A custom set class with a custom maxlen is a thought
game_stream_dict = defaultdict(partial(deque, maxlen=100))


def getStreams(count=1, game_id=None):
    results = []
    no_duplicates = set()

    # get a random game that matches the game_id
    if game_id:
        stream = choice(list(game_stream_dict[game_id]))
        return json.loads(stream)

    # remove duplicates
    for i in range(int(count)):
        key = main_redis.randomkey()
        no_duplicates.add(key)

    for key in no_duplicates:
        if not key:
            return results

        stream = json.loads(key)
        stream['fetched'] = main_redis.get(key)
        stream['ttl'] = main_redis.ttl(key)

        # with every random stream found, use it to populate game_stream_dict
        if stream['game_id']:
            game_stream_dict[stream['game_id']].append(key)
        results.append(stream)
    return results


def getGames():
    """
    Return all the games that are being streamed by people with 0 followers.
    """
    games = []

    # take a copy of the ids so they don't change during iteration
    game_ids = game_stream_dict.copy().keys()

    for k in game_ids:
        game = game_redis.get(k)
        if not game:
            continue
        games.append(json.loads(game))
    return games


@app.route('/')
def root():
    return app.send_static_file('index-from-scratch.html')


@app.route('/stream', defaults={'game_id': None})
@app.route('/stream/<game_id>')
def get_stream(game_id):
    if game_id:
        return getStreams(1, game_id)
    streams = getStreams()
    if streams:
        return streams[0]
    return '{}'


@app.route('/streams', defaults={'count': 20})
@app.route('/streams/<count>')
def get_streams(count):
    streams = getStreams(count)

    if streams:
        return jsonify(streams)
    return '[]'


@app.route('/games')
def get_games():
    games = getGames()
    if games:
        return jsonify(games)
    return '[]'


@app.route('/stats/json')
@app.route('/stats.json')
def get_stats_json():
    stats = json.loads(stats_redis.get('stats'))
    stats['streams'] = main_redis.dbsize()

    return jsonify(stats)


@app.route('/status')
@app.route('/stats')
@app.route('/stats.txt')
def get_stats_human():
    stats = json.loads(stats_redis.get('stats'))

    return (f"{int(stats['ratelimit_remaining'])}/{int(stats['ratelimit_limit'])} API tokens left "
            f"({round((1 - int(stats['ratelimit_remaining']) / int(stats['ratelimit_limit'])) * 100, 2)}% spent). "
            f"{main_redis.dbsize() - 1} streams loaded."
            )


if __name__ == "__main__":
    # populate game filter
    getStreams(100)
    app.run(debug=True)

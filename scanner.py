#!/usr/bin/env python3

import json
import logging
import os
import sys
import time

import redis
import requests

logging.basicConfig(stream=sys.stdout, level=logging.INFO)

# found at https://dev.twitch.tv/console
CLIENT_ID = os.environ.get('CLIENT_ID')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET')

if not CLIENT_ID or not CLIENT_SECRET:
    logging.error('CLIENT_ID or CLIENT_SECRET not set correctly! Exiting...')
    exit(1)

MAX_VIEWERS = 0  # number of viewers to be considered for inclusion
REQUEST_LIMIT = 1500  # number of API requests to stop at before starting a new search
MINIMUM_STREAMS_TO_GET = 50  # if REQUEST_LIMIT streams doesn't capture at least this many zero-
# viewer streams, keep going
SECONDS_BEFORE_RECORD_EXPIRATION = 180  # how many seconds a stream should stay in redis

main_redis = redis.Redis(decode_responses=True, db=0)
stats_redis = redis.Redis(decode_responses=True, db=1)
game_redis = redis.Redis(decode_responses=True, db=2)


def get_bearer_token(client_id, secret):
    payload = {
        'client_id':     client_id,
        'client_secret': secret,
        'grant_type':    'client_credentials'
    }
    token_response = requests.post('https://id.twitch.tv/oauth2/token', params=payload)

    logging.debug(f"Issuing token request to {token_response.url}")

    try:
        logging.debug(
            f"Recieved {token_response.json()['access_token']}; expires in {token_response.json()['expires_in']}s")
        return (token_response.json()['access_token'])
    except KeyError:
        logging.error(f"Didn't find access token. Got '{token_response.text}'")
        return None


def get_stream_list_response(client_id, token, pagination_offset=None):
    headers = {
        'client-id':     client_id,
        'Authorization': f'Bearer {token}'
    }

    url_params = {
        'first':    '100',
        'language': 'en'
    }
    if pagination_offset:
        url_params['after'] = pagination_offset

    stream_list = requests.get('https://api.twitch.tv/helix/streams', headers=headers,
                               params=url_params)
    return stream_list


def get_top_games_response(token, pagination_offset=None):
    # https://api.twitch.tv/helix/games/top
    headers = {
        'client-id':     CLIENT_ID,
        'Authorization': f'Bearer {token}'
    }
    url_params = {
        'first': '100'
    }
    if pagination_offset:
        url_params['after'] = pagination_offset

    game_list = requests.get('https://api.twitch.tv/helix/games/top', headers=headers,
                             params=url_params)

    return game_list


def populate_streamers(client_id, client_secret):
    token = get_bearer_token(client_id, client_secret)

    if not token:
        logging.error("There's no token! Halting.")
        return

    requests_sent = 1
    streams_grabbed = 0

    # eat page after page of API results until we hit our request limit
    stream_list = get_stream_list_response(client_id, token)
    while requests_sent <= REQUEST_LIMIT or streams_grabbed < MINIMUM_STREAMS_TO_GET:
        stream_list_data = stream_list.json()
        requests_sent += 1

        # filter out streams with our desired count and inject into redis
        streams_found = list(filter(lambda stream: int(stream['viewer_count']) <= MAX_VIEWERS,
                                    stream_list_data['data']))
        for stream in streams_found:
            streams_grabbed += 1
            main_redis.setex(json.dumps(stream), SECONDS_BEFORE_RECORD_EXPIRATION, time.time())

        # report on what we inserted
        if len(streams_found) > 0:
            logging.debug(f"Inserted {len(streams_found)} streams")

        # sleep on rate limit token utilization
        rate_limit_usage = round((1 - int(stream_list.headers['Ratelimit-Remaining']) / int(
            stream_list.headers['Ratelimit-Limit'])) * 100)
        if rate_limit_usage > 60:
            logging.warning(f"Rate limiting is at {rate_limit_usage}% utilized; sleeping for 30s")
            time.sleep(30)

        # drop a status every now and again
        if requests_sent % 10 == 0:
            logging.info((f"{requests_sent} requests sent ({streams_grabbed} streams found); "
                          f"{stream_list.headers['Ratelimit-Remaining']} of {stream_list.headers['Ratelimit-Limit']} "
                          f"API tokens remaining ({rate_limit_usage}% utilized)"))
            stats_redis.set('stats',
                            json.dumps({
                                'ratelimit_remaining': stream_list.headers['Ratelimit-Remaining'],
                                'ratelimit_limit':     stream_list.headers['Ratelimit-Limit'],
                                'time_of_ratelimit':   time.time()
                            })
                            )

        # aaaaand do it again
        try:
            pagination_offset = stream_list_data['pagination']['cursor']
        except KeyError:
            # we hit the end of the list; no more keys
            logging.warning(f"Hit end of search results")
            break
        stream_list = get_stream_list_response(client_id, token, pagination_offset)


def populate_top_games():
    """
    Grab all games off of twitch and add that to redis. Tried to stay with existing coding style
    """
    token = get_bearer_token(CLIENT_ID, CLIENT_SECRET)
    if not token:
        logging.error("There's no token! Halting.")
        return

    requests_sent = 1
    games_grabbed = 0

    # eat page after page of API results until we hit our request limit
    top_game_list = get_top_games_response(token)
    while True:
        top_game_data: dict = top_game_list.json()
        requests_sent += 1

        for game in top_game_data['data']:
            games_grabbed += 1
            game_redis.set(game['id'], json.dumps(game))

            # report on what we inserted
        if len(top_game_data['data']) > 0:
            logging.debug(f"Inserted {len(top_game_data)} streams")

            # sleep on rate limit token utilization
        rate_limit_usage = round((1 - int(top_game_list.headers['Ratelimit-Remaining']) / int(
            top_game_list.headers['Ratelimit-Limit'])) * 100)
        if rate_limit_usage > 60:
            logging.warning(f"Rate limiting is at {rate_limit_usage}% utilized; sleeping for 30s")
            time.sleep(30)

        # drop a status every now and again
        if requests_sent % 10 == 0:
            logging.info((f"{requests_sent} requests sent ({games_grabbed} games found); "
                          f"{top_game_list.headers['Ratelimit-Remaining']} of {top_game_list.headers['Ratelimit-Limit']} "
                          f"API tokens remaining ({rate_limit_usage}% utilized)"))
            stats_redis.set('stats',
                            json.dumps({
                                'ratelimit_remaining': top_game_list.headers['Ratelimit-Remaining'],
                                'ratelimit_limit':     top_game_list.headers['Ratelimit-Limit'],
                                'time_of_ratelimit':   time.time()
                            }))

            # aaaaand do it again
            try:
                pagination_offset = top_game_data['pagination']['cursor']
            except KeyError:
                # we hit the end of the list; no more keys
                logging.warning(f"Hit end of game search results")
                break
            top_game_list = get_top_games_response(token, pagination_offset)


if __name__ == '__main__':
    game_redis.flushdb()
    populate_top_games()
    while True:
        populate_streamers(CLIENT_ID, CLIENT_SECRET)

import json
import os

import simplejson
import pysolr
import elasticsearch
from flask import Flask, render_template, request, abort, Response


app = Flask(__name__)
DEBUG = os.environ.get('DEBUG', False)
PORT = os.environ.get('PHOTON_PORT', 5001)
SUPPORTED_LANGUAGES = ['de', 'en', 'fr', 'it']

solr = pysolr.Solr(os.environ.get("SOLR_ENDPOINT", 'http://localhost:8983/solr/testing'), timeout=10)

es = elasticsearch.Elasticsearch()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/')
def api():
    params = {}

    lang = request.args.get('lang')
    if lang is None or lang not in SUPPORTED_LANGUAGES:
        lang = 'en'

    try:
        lon = float(request.args.get('lon'))
        lat = float(request.args.get('lat'))
    except (TypeError, ValueError):
        lon = lat = None

    try:
        limit = min(int(request.args.get('limit')), 50)
    except (TypeError, ValueError):
        limit = 15

    query = request.args.get('q')
    if not query:
        abort(400, "missing search term 'q': /?q=berlin")

    req_body = {
        "dis_max": {
            "queries": [
                {
                    "match": {
                        "collector.{0}".format(lang): {
                            "query": query,
                            "operator": "or",
                            "analyzer": "raw_stringanalyser"
                        }
                    }
                },
                {
                    "match": {
                        "collector.{0}.raw".format(lang): {
                            "query": query,
                            "operator": "or",
                            "analyzer": "raw_stringanalyser"}
                    }
                }
            ]
        }
    }

    if lon is not None and lat is not None:
        req_body = {
            "function_score": {
                "boost_mode": "replace",
                "query": req_body,
                "script_score": {
                    "params": {
                        "lat": 2,
                        "lon": 3.1
                    },
                    "script": "_score / (doc['coordinate'].distance(lat, lon)/1000 + 1) + 0.5"
                }
            }
        }

    body = {"query": req_body, "size": limit}
    print(json.dumps(body))
    results = es.search(index="photon", body=body)

    data = json.dumps(to_geo_json(results['hits']['hits'], lang=lang))
    return Response(data, mimetype='application/json')


@app.route('/api/solr/')
def api_solr():
    params = {}

    lang = request.args.get('lang')
    if lang is None or lang not in SUPPORTED_LANGUAGES:
        lang = 'en'

    try:
        lon = float(request.args.get('lon'))
        lat = float(request.args.get('lat'))
        params['qt'] = '{lang}_loc'.format(lang=lang)
        params['pt'] = '{lat},{lon}'.format(lon=lon, lat=lat)
    except (TypeError, ValueError):
        params['qt'] = '{lang}'.format(lang=lang)

    try:
        params['rows'] = min(int(request.args.get('limit')), 50)
    except (TypeError, ValueError):
        params['rows'] = 15

    query = request.args.get('q')
    if not query:
        abort(400, "missing search term 'q': /?q=berlin")

    results = solr.search(query, **params)
    data = json.dumps(to_geo_json_solr(results.docs, lang=lang))
    return Response(data, mimetype='application/json')


def housenumber_first(lang):
    if lang in ['de', 'it']:
        return False

    return True


def to_geo_json(hits, lang='en'):
    features = []

    for hit in hits:
        source = hit['_source']

        properties = {}

        if 'osm_id' in source:
            properties['osm_id'] = int(source['osm_id'])

        for attr in ['osm_key', 'osm_value', 'street', 'postcode', 'housenumber']:
            if attr in source:
                properties[attr] = source[attr]

        # language specific mapping
        for attr in ['name', 'country', 'city']:
            obj = source.get(attr, {})
            value = obj.get(lang) or obj.get('default')
            if value:
                properties[attr] = value

        if not 'name' in properties and 'housenumber' in properties:
            if housenumber_first(lang):
                properties['name'] = properties['housenumber'] + ' ' + properties['street']
            else:
                properties['name'] = properties['street'] + ' ' + properties['housenumber']

        coordinates = [float(el) for el in source['coordinate'].split(',')]
        coordinates.reverse()

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": coordinates
            },
            "properties": properties
        }

        features.append(feature)

    return {
        "type": "FeatureCollection",
        "features": features
    }


def to_geo_json_solr(docs, lang='en'):
    features = []

    for doc in docs:
        properties = {}
        for attr in ['osm_id', 'osm_key', 'osm_value', 'street', 'postcode', 'housenumber']:
            if attr in doc:
                properties[attr] = doc[attr]

        # language specific mapping
        for attr in ['name', 'country', 'city']:
            lang_attr = attr + "_" + lang
            value = doc.get(lang_attr) or doc.get(attr)
            if value:
                properties[attr] = value

        if not 'name' in properties and 'housenumber' in properties:
            if housenumber_first(lang):
                properties['name'] = properties['housenumber'] + ' ' + properties['street']
            else:
                properties['name'] = properties['street'] + ' ' + properties['housenumber']

        coordinates = [float(el) for el in doc['coordinate'].split(',')]
        coordinates.reverse()

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": coordinates
            },
            "properties": properties
        }

        features.append(feature)

    return {
        "type": "FeatureCollection",
        "features": features
    }


@app.route('/search/')
def search():
    params = {
        "hl": 'true',
        "rows": 10
    }
    results = solr.search(request.args.get('q', '*:*'), **params)
    return simplejson.dumps({
        "docs": results.docs,
        "highlight": results.highlighting
    })


if __name__ == "__main__":
    app.run(debug=DEBUG, port=PORT)
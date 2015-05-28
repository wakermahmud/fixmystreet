#!/usr/bin/env python
import os
import sys
from itertools import groupby, islice
from csv import DictWriter

import yaml
import sodapy
import psycopg2
import psycopg2.extras
import rabx


yml_path = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "conf", "general.yml"
))
with open(yml_path) as f:
    config = yaml.load(f)


PHOTO_URL = "http://collideosco.pe/photo/{id}.full.jpeg?{photo}"

def get_cursor():
    db = psycopg2.connect( "host='{host}' dbname='{name}' user='{user}' password='{password}'".format(
        host=config['FMS_DB_HOST'],
        name=config['FMS_DB_NAME'],
        user=config['FMS_DB_USER'],
        password=config['FMS_DB_PASS']
    ))
    cursor = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    return cursor

def socrata_connect():
    return sodapy.Socrata(
        "mysociety.azure-westeurope-prod.socrata.com",
        config['SOCRATA_APP_TOKEN'],
        username=config['SOCRATA_USERNAME'],
        password=config['SOCRATA_PASSWORD']
    )

def query_recent_reports(cursor, limit=100):
    """
    Performs a query on the cursor to get
    the most recent `limit` reports from the DB, ordered
    by ascending order of confirmation date.
    """
    query = "SELECT id, latitude, longitude, category, title, extra, " \
            "detail, confirmed as reported, lastupdate, whensent, " \
            "bodies_str as body, photo, non_public, state " \
            "FROM problem " \
            "WHERE state IN ('confirmed', 'hidden') " \
            "ORDER BY reported DESC"
    params = []
    if limit:
        query = "{query} LIMIT %s".format(query=query)
        params = [limit]
    cursor.execute(query, params)

def transform_results(cursor, csv=False):
    """
    Returns an iterable of dicts suitable for uploading to Socrata.
    This must be called immediately after query_recent_reports because
    it operates on results held in the cursor.
    """
    for row in cursor:
        if row['non_public'] or row['state'] == "hidden":
            if csv:
                # don't include hidden reports in the CSV
                continue
            else:
                row = {
                    ':id': row['id'],
                    ':deleted': True
                }
        else:
            row['url'] = "http://collideosco.pe/report/{id}".format(id=row['id'])
            if not csv:
                # Convert the raw lat/lon columns into a dict as expected by SODA
                row['location'] = {'longitude': row['longitude'], 'latitude': row['latitude']}
                del row['longitude'], row['latitude']
            # Date fields aren't handled by the JSON encoder, so do it manually
            row['reported'] = row['reported'].isoformat()
            row['lastupdate'] = row['lastupdate'].isoformat()
            if row['whensent']:
                row['whensent'] = row['whensent'].isoformat()
            # Photo is stored as a 40-byte ID which needs appending to a URL base
            row['photo_url'] = PHOTO_URL.format(id=row['id'], photo=row['photo']) if row.get("photo") else None
            del row['photo']
            # Make sure only one receiving body is included
            if row.get("body"):
                row['body'] = row['body'].split(",")[0]
            del row['state'], row['non_public']
            # Collideoscope stores details about the incident in the 'extra' field
            extra = rabx.unserialise(row['extra'])
            del row['extra']
            row['severity'] = extra.get("severity")
            row['road_type'] = extra.get("road_type")
            row['injury_detail'] = extra.get("injury_detail")
            row['participants'] = extra.get("participants")
            row['media_url'] = extra.get("media_url")
            row['incident_date'] = " ".join((extra.get("incident_date", ""), extra.get("incident_time", "")))
        yield row

def upload_reports(socrata, reports):
    """
    The API chokes if you send too many items at once, so split the
    reports we want to send into smaller batches
    """
    batch_size = 50
    for k, group in groupby(enumerate(reports), lambda x: x[0] // batch_size):
        reports_batch = [i[1] for i in group]
        print socrata.upsert(config['SOCRATA_DATASET_URL'], reports_batch)

def write_csv(reports, limit):
    with open("output.csv", "wb") as f:
        writer = None
        for row in islice(reports, limit):
            if writer is None:
                writer = DictWriter(f, row.keys())
                writer.writeheader()
            writer.writerow(row)

def main():
    cursor = get_cursor()
    query_recent_reports(cursor, limit=None)

    # Are we producing an initial kickstart CSV for uploading to Socrata?
    if len(sys.argv) > 1 and sys.argv[1] == "csv":
        try:
            limit = int(sys.argv[2])
        except (IndexError, ValueError):
            limit = 10
        reports = transform_results(cursor, csv=True)
        write_csv(reports, limit)
    else:
        reports = transform_results(cursor)
        socrata = socrata_connect()
        upload_reports(socrata, reports)


if __name__ == '__main__':
    main()
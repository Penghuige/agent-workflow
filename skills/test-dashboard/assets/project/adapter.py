#!/usr/bin/env python3
"""Example only: replace this neutral document with collection from real sources.

Run this script separately to create a candidate file. Validate the document with
the central validator against the final HTML output directory, then atomically
replace the configured normalized source. Do not redirect over a live source.
Add a normalized source pointing at the published file to config.json. The builder
does not execute this script. Put evidence under the final HTML directory and
use attachment paths relative to that directory; building does not copy evidence.
"""

import json


def collect():
    return {
        'schema_version': 1,
        'project': {'name': 'Replace with project name'},
        'runs': [{
            'id': 'project-records', 'label': 'Project collector example',
            'state': 'not_integrated', 'inventory_known': False, 'cases': [],
            'notes': ['Replace this example with records from real project sources.'],
        }],
    }


if __name__ == '__main__':
    print(json.dumps(collect(), ensure_ascii=False, indent=2))

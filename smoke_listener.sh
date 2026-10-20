#!/bin/bash

BASEDIR=`dirname "$0"`

if [[ ! -e $BASEDIR/.venv ]]; then
    echo "Creating virtual environment..."
    python3 -m venv $BASEDIR/.venv
    source $BASEDIR/.venv/bin/activate
    pip install -r $BASEDIR/requirements.txt
else
    source $BASEDIR/.venv/bin/activate
fi

python3 $BASEDIR/smoke_listener.py

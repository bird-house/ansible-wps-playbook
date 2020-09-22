#!/bin/bash
# clean pywps outputs
find /var/lib/pywps/outputs/rook/* -type d | xargs rm -rf


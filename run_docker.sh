#!/usr/bin/env bash
docker run  -v `pwd`:/src -w /src -p 5000:5000 -it --rm  ubuntu bash

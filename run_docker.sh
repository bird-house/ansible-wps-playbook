#!/usr/bin/env bash
if [ -z "$1" ]; then
    OS='ubuntu'
else
  OS=$1
fi
echo "starting: $OS"
docker run  -v `pwd`:/src -w /src -p 5000:5000 -it --rm  $OS bash

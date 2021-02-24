#!/usr/bin/env python3

import json
import requests

URL = "https://ip-ranges.amazonaws.com/ip-ranges.json"
SERVICE = "ROUTE53_HEALTHCHECKS"


def get_route53_ips():
    """
    Fetch json document with current ip-ranges for aws route53 load-balancer:
    * https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/route-53-ip-addresses.html
    * https://docs.aws.amazon.com/general/latest/gr/aws-ip-ranges.html

    Check nginx access log:
    grep Amazon-Route53-Health-Check-Service /var/log/nginx/access.log | cut -d " " -f 1 | sort | uniq
    """
    data = requests.get(URL).json()
    print(f"create date={data['createDate']}")
    print("ipv4 ranges:")
    for prefix in data['prefixes']:
        if prefix['service'] == SERVICE:
            print(prefix['ip_prefix'])
    print("\nipv6 ranges:")
    for prefix in data['ipv6_prefixes']:
        if prefix['service'] == SERVICE:
            print(prefix['ipv6_prefix'])


if __name__ == "__main__":
    get_route53_ips()

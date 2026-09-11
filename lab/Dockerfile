FROM netboxcommunity/netbox:v4.7.0-5.1.0@sha256:a2cdf00fab61d2ae37e4f987adaa403fad5c4049a63bc960768b7bbf804e2cb6

RUN /usr/local/bin/uv pip install netboxlabs-diode-netbox-plugin==1.17.0
COPY configuration/plugins.py /etc/netbox/config/plugins.py
RUN DEBUG=true SECRET_KEY=build-only-key-with-minimum-length-not-a-runtime-secret \
    /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py collectstatic --no-input

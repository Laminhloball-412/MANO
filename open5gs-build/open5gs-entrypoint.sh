#!/bin/sh
# Tương thích với cách free5gmano/open5gs khởi động
DB_URI=${DB_URI:-mongodb://mongodb-svc/open5gs}
find /open5gs/install/etc/open5gs -name "*.yaml" -exec \
    sed -i "s|mongodb://localhost/open5gs|${DB_URI}|g" {} \;

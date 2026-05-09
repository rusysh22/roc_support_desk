#!/bin/bash
# Patch nginx rocdesk config to support WebSocket upgrade
set -e

CONF="/etc/nginx/sites-available/rocdesk"

# Add /ws/ location block before the / block
sudo sed -i 's|    location / {|    location /ws/ {\n        proxy_pass http://localhost:8000;\n        proxy_http_version 1.1;\n        proxy_set_header Upgrade $http_upgrade;\n        proxy_set_header Connection "upgrade";\n        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto $scheme;\n        proxy_read_timeout 86400;\n    }\n\n    location / {|' "$CONF"

sudo nginx -t && sudo systemctl reload nginx
echo "Done. WebSocket proxy configured."

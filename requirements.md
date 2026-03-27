To connect your company's live Prometheus and Grafana servers, you will need to provide the following credentials in your .env file (or as environment variables).

Based on the configuration we've built, here is the exact list of what you need:

1. Prometheus Credentials
Prometheus usually requires a URL and, depending on your company's security, a token or basic auth.

PROMETHEUS_URL: The base URL of your Prometheus server (e.g., https://prometheus.mycompany.com).
PROMETHEUS_USERNAME & PROMETHEUS_PASSWORD: If your company uses Basic Authentication.
PROMETHEUS_TOKEN: If your company uses a Bearer Token (like an API token).
PROMETHEUS_VERIFY_CERTS: Set to True if using internal SSL certificates (or False to skip verification for testing).
2. Grafana Credentials
To search dashboards and create visualizations, the agent needs an API Key or Service Account Token.

GRAFANA_URL: The base URL of your Grafana server (e.g., https://grafana.mycompany.com).
GRAFANA_API_KEY: A Service Account Token with Editor or Admin permissions (so it can create/delete ad-hoc dashboards).
GRAFANA_ORG_ID: Usually 1 by default, but check your Grafana settings if you have multiple organizations.
GRAFANA_ADMIN_USER & GRAFANA_ADMIN_PASSWORD: (Optional) Only if you want the agent to perform administrative tasks like folder creation.
How to set them up:
Create a file named .env in the root of the project (if it's not already there) and add these lines:

bash
# Prometheus Connection
INFRA_PROMETHEUS_URL=http://your-prometheus-ip:9090
INFRA_PROMETHEUS_TOKEN=your_token_here
INFRA_PROMETHEUS_VERIFY_CERTS=True
# Grafana Connection
INFRA_GRAFANA_URL=http://your-grafana-ip:3000
INFRA_GRAFANA_API_KEY=your_grafana_service_account_token
INFRA_GRAFANA_ORG_ID=1
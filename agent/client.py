import os

import requests


class InvalidAcknowledgement(requests.RequestException):
    pass


def send_metrics(metrics):
    response = requests.post(
        os.environ.get("TERO_API_URL", "http://127.0.0.1:8000/metrics"),
        json=metrics,
        timeout=(3, 15),
        allow_redirects=False,
    )
    try:
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError as error:
            raise InvalidAcknowledgement("ACK no es JSON") from error
        if (response.status_code != 200 or not isinstance(body, dict)
                or body.get("measurement_id") != metrics.get("measurement_id")
                or not isinstance(body.get("duplicate"), bool)):
            raise InvalidAcknowledgement("ACK no corresponde a la muestra")
        return response
    finally:
        response.close()

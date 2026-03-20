import logging, requests, json
from datetime import datetime, timezone, timedelta
from flask import Flask, request as flask_request, jsonify

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

BASE    = "https://hourlypricing.comed.com/api"
CENTRAL = timezone(timedelta(hours=-5))

def comed_fetch(t):
    try:
        r = requests.get(BASE, params={"type": t, "format": "json"}, timeout=10)
        r.raise_for_status()
        d = r.json()
        return d if isinstance(d, list) and d else None
    except Exception as e:
        logging.error("ComEd error [%s]: %s", t, e)
        return None

def fmt_time(ms):
    try:
        dt = datetime.fromtimestamp(int(ms)/1000, tz=timezone.utc).astimezone(CENTRAL)
        return dt.strftime("%-I:%M %p")
    except:
        return "unknown time"

def lbl(p):
    if p < 0:  return "negative, you are being credited for using electricity"
    if p < 3:  return "very low"
    if p < 6:  return "low"
    if p < 9:  return "moderate"
    if p < 15: return "elevated"
    return "high, consider shifting heavy appliance use"

def respond(text, end_session=True):
    return jsonify({
        "version": "1.0",
        "response": {
            "outputSpeech": {"type": "PlainText", "text": text},
            "shouldEndSession": end_session
        }
    })

def keep(text):
    return respond(text, end_session=False)

@app.route("/alexa", methods=["POST"])
def alexa():
    body = flask_request.get_json(force=True)
    req_type = body.get("request", {}).get("type", "")
    intent_name = body.get("request", {}).get("intent", {}).get("name", "")

    if req_type == "LaunchRequest":
        return keep("Welcome to energy price check. Ask me for the current price, hour average, day ahead prices, a full summary, best time to run appliances, recent price history, or whether it is a good time to use electricity.")

    if req_type == "SessionEndedRequest":
        return jsonify({"version": "1.0", "response": {}})

    if req_type == "IntentRequest":

        if intent_name == "FiveMinutePriceIntent":
            d = comed_fetch("5minutefeed")
            if not d: return respond("Sorry, I could not reach the ComEd API. Please try again.")
            p = float(d[0]["price"]); t = fmt_time(d[0]["millisUTC"])
            return respond(f"The latest five-minute price as of {t} Central Time is {p:.1f} cents per kilowatt-hour, {lbl(p)}.")

        if intent_name == "CurrentHourAverageIntent":
            d = comed_fetch("currenthouraverage")
            if not d: return respond("Sorry, the ComEd API is unavailable right now.")
            p = float(d[0]["price"])
            return respond(f"The current hour average is {p:.1f} cents per kilowatt-hour, {lbl(p)}.")

        if intent_name == "DayAheadPricesIntent":
            d = comed_fetch("daytahead")
            if not d: return respond("Day ahead prices are not available yet. ComEd publishes them in the evening.")
            prices = [float(x["price"]) for x in d]
            avg = sum(prices)/len(prices)
            cheapest = sorted(d, key=lambda x: float(x["price"]))[:3]
            times = ", ".join(fmt_time(x["millisUTC"]) for x in cheapest)
            return respond(f"Day ahead prices cover {len(prices)} hours. Average is {avg:.1f} cents, ranging from {min(prices):.1f} to {max(prices):.1f} cents. The three cheapest hours are around {times} Central Time.")

        if intent_name == "PriceSummaryIntent":
            five = comed_fetch("5minutefeed"); avg = comed_fetch("currenthouraverage"); da = comed_fetch("daytahead")
            parts = []
            if five:
                p = float(five[0]["price"]); parts.append(f"Latest five-minute price is {p:.1f} cents, {lbl(p)}.")
            else: parts.append("Five-minute price unavailable.")
            if avg:
                p = float(avg[0]["price"]); parts.append(f"Current hour average is {p:.1f} cents, {lbl(p)}.")
            else: parts.append("Hour average unavailable.")
            if da:
                ps = [float(x["price"]) for x in da]; a = sum(ps)/len(ps)
                parts.append(f"Day ahead average is {a:.1f} cents, ranging {min(ps):.1f} to {max(ps):.1f} cents.")
            else: parts.append("Day ahead prices not yet published.")
            return respond(" ".join(parts))

        if intent_name == "BestTimeIntent":
            da = comed_fetch("daytahead"); feed = comed_fetch("5minutefeed")
            d = da or feed
            if not d: return respond("Pricing data is unavailable right now.")
            src = "day ahead forecast" if da else "recent five-minute prices"
            best = min(d, key=lambda x: float(x["price"]))
            p = float(best["price"]); t = fmt_time(best["millisUTC"])
            return respond(f"Based on the {src}, the best time to run large appliances is around {t} Central Time at {p:.1f} cents per kilowatt-hour, {lbl(p)}.")

        if intent_name == "RecentPriceHistoryIntent":
            d = comed_fetch("5minutefeed")
            if not d: return respond("Historical pricing data is unavailable right now.")
            prices = [float(x["price"]) for x in d]
            avg = sum(prices)/len(prices); neg = sum(1 for p in prices if p < 0)
            msg = f"Over the last twenty-four hours prices averaged {avg:.1f} cents. Low was {min(prices):.1f} cents, high was {max(prices):.1f} cents. "
            if neg: msg += f"There were {neg} intervals with negative prices."
            return respond(msg)

        if intent_name == "IsItGoodTimeIntent":
            d = comed_fetch("5minutefeed") or comed_fetch("currenthouraverage")
            if not d: return respond("I could not get the current price. Please try again.")
            p = float(d[0]["price"])
            if p < 0:   msg = "Yes, absolutely! Prices are negative. You are being credited for every kilowatt-hour you use. Run your appliances now."
            elif p < 4: msg = f"Yes, great time. At {p:.1f} cents the price is very low. Go ahead and run your appliances."
            elif p < 9: msg = f"Moderate time at {p:.1f} cents. You could run appliances, but waiting for a lower window saves more."
            else:       msg = f"Prices are elevated at {p:.1f} cents. Consider waiting before running your washer, dryer, or dishwasher."
            return respond(msg)

        if intent_name == "AMAZON.HelpIntent":
            return keep("You can ask: current price, hour average, day ahead prices, full summary, best time to run appliances, recent price history, or is it a good time to use electricity.")

        if intent_name in ("AMAZON.StopIntent", "AMAZON.CancelIntent"):
            return respond("Goodbye! Shift your energy use to low-price hours and save on your bill.")

    return respond("Sorry, I did not understand that. Please try again.")

@app.route("/", methods=["GET"])
def health():
    return '{"status":"ComEd Alexa skill is running"}', 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

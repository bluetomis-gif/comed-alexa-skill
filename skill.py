import logging, requests
from datetime import datetime, timezone, timedelta
from flask import Flask, request as flask_request
from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.dispatch_components import AbstractRequestHandler, AbstractExceptionHandler
from ask_sdk_core.utils import is_intent_name, is_request_type
from ask_sdk_core.serialize import DefaultSerializer
from ask_sdk_model import RequestEnvelope

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)
sb  = SkillBuilder()
SER = DefaultSerializer()
BASE    = 'https://hourlypricing.comed.com/api'
CENTRAL = timezone(timedelta(hours=-5))

def comed_fetch(t):
    try:
        r = requests.get(BASE, params={'type': t, 'format': 'json'}, timeout=10)
        r.raise_for_status()
        d = r.json()
        return d if isinstance(d, list) and d else None
    except Exception as e:
        logging.error('ComEd error [%s]: %s', t, e); return None

def fmt_time(ms):
    try:
        dt = datetime.fromtimestamp(int(ms)/1000, tz=timezone.utc).astimezone(CENTRAL)
        return dt.strftime('%-I:%M %p')
    except: return 'unknown time'

def lbl(p):
    if p < 0:  return 'negative, you are being credited for using electricity'
    if p < 3:  return 'very low'
    if p < 6:  return 'low'
    if p < 9:  return 'moderate'
    if p < 15: return 'elevated'
    return 'high, consider shifting heavy appliance use'

def end(hi, text):
    hi.response_builder.speak(text).set_should_end_session(True)
    return hi.response_builder.response

def keep(hi, text, reprompt='What else would you like to know?'):
    hi.response_builder.speak(text).ask(reprompt).set_should_end_session(False)
    return hi.response_builder.response

class LaunchHandler(AbstractRequestHandler):
    def can_handle(self, hi): return is_request_type('LaunchRequest')(hi)
    def handle(self, hi):
        return keep(hi, 'Welcome to ComEd Hourly Pricing. Ask me for the current price, the hour average, day-ahead prices, a full summary, the best time to run appliances, the last 24 hours, or whether it is a good time to use electricity.')

class FiveMinuteHandler(AbstractRequestHandler):
    def can_handle(self, hi): return is_intent_name('FiveMinutePriceIntent')(hi)
    def handle(self, hi):
        d = comed_fetch('5minutefeed')
        if not d: return end(hi, 'Sorry, I could not reach the ComEd API. Please try again.')
        p = float(d[0]['price']); t = fmt_time(d[0]['millisUTC'])
        return end(hi, f'The latest five-minute price as of {t} Central Time is {p:.1f} cents per kilowatt-hour, {lbl(p)}.')

class HourAverageHandler(AbstractRequestHandler):
    def can_handle(self, hi): return is_intent_name('CurrentHourAverageIntent')(hi)
    def handle(self, hi):
        d = comed_fetch('currenthouraverage')
        if not d: return end(hi, 'Sorry, the ComEd API is unavailable right now.')
        p = float(d[0]['price'])
        return end(hi, f'The current hour average is {p:.1f} cents per kilowatt-hour, {lbl(p)}. This is the running average of all five-minute prices so far this hour.')

class DayAheadHandler(AbstractRequestHandler):
    def can_handle(self, hi): return is_intent_name('DayAheadPricesIntent')(hi)
    def handle(self, hi):
        d = comed_fetch('daytahead')
        if not d: return end(hi, 'Day-ahead prices are not available yet. ComEd publishes them in the evening.')
        prices = [float(x['price']) for x in d]
        avg = sum(prices)/len(prices)
        cheapest = sorted(d, key=lambda x: float(x['price']))[:3]
        times = ', '.join(fmt_time(x['millisUTC']) for x in cheapest)
        return end(hi, f'Day-ahead prices cover {len(prices)} hours. Average is {avg:.1f} cents, ranging from {min(prices):.1f} to {max(prices):.1f} cents. The three cheapest hours are around {times} Central Time.')

class SummaryHandler(AbstractRequestHandler):
    def can_handle(self, hi): return is_intent_name('PriceSummaryIntent')(hi)
    def handle(self, hi):
        five = comed_fetch('5minutefeed'); avg = comed_fetch('currenthouraverage'); da = comed_fetch('daytahead')
        parts = []
        if five:
            p = float(five[0]['price']); parts.append(f'Latest five-minute price is {p:.1f} cents, {lbl(p)}.')
        else: parts.append('Five-minute price unavailable.')
        if avg:
            p = float(avg[0]['price']); parts.append(f'Current hour average is {p:.1f} cents, {lbl(p)}.')
        else: parts.append('Hour average unavailable.')
        if da:
            ps = [float(x['price']) for x in da]; a = sum(ps)/len(ps)
            parts.append(f'Day-ahead average is {a:.1f} cents, ranging {min(ps):.1f} to {max(ps):.1f} cents.')
        else: parts.append('Day-ahead prices not yet published.')
        return end(hi, ' '.join(parts))

class BestTimeHandler(AbstractRequestHandler):
    def can_handle(self, hi): return is_intent_name('BestTimeIntent')(hi)
    def handle(self, hi):
        da = comed_fetch('daytahead'); feed = comed_fetch('5minutefeed')
        d = da or feed
        if not d: return end(hi, 'Pricing data is unavailable right now.')
        src = 'day-ahead forecast' if da else 'recent five-minute prices'
        best = min(d, key=lambda x: float(x['price']))
        p = float(best['price']); t = fmt_time(best['millisUTC'])
        return end(hi, f'Based on the {src}, the best time to run large appliances is around {t} Central Time at {p:.1f} cents per kilowatt-hour, {lbl(p)}.')

class Last24Handler(AbstractRequestHandler):
    def can_handle(self, hi): return is_intent_name('RecentPriceHistoryIntent')(hi)
    def handle(self, hi):
        d = comed_fetch('5minutefeed')
        if not d: return end(hi, 'Historical pricing data is unavailable right now.')
        prices = [float(x['price']) for x in d]
        avg = sum(prices)/len(prices); neg = sum(1 for p in prices if p < 0)
        msg = f'Over the last 24 hours prices averaged {avg:.1f} cents. Low was {min(prices):.1f} cents, high was {max(prices):.1f} cents. '
        if neg: msg += f'There were {neg} intervals with negative prices.'
        return end(hi, msg)

class GoodTimeHandler(AbstractRequestHandler):
    def can_handle(self, hi): return is_intent_name('IsItGoodTimeIntent')(hi)
    def handle(self, hi):
        d = comed_fetch('5minutefeed') or comed_fetch('currenthouraverage')
        if not d: return end(hi, 'I could not get the current price. Please try again.')
        p = float(d[0]['price'])
        if p < 0:   msg = 'Yes, absolutely! Prices are negative, you are being credited for every kilowatt-hour you use. Run your appliances now.'
        elif p < 4: msg = f'Yes, great time. At {p:.1f} cents the price is very low. Go ahead and run your appliances.'
        elif p < 9: msg = f'Moderate time at {p:.1f} cents. You could run appliances, but waiting for a lower window saves more.'
        else:       msg = f'Prices are elevated at {p:.1f} cents. Consider waiting before running your washer, dryer, or dishwasher.'
        return end(hi, msg)

class HelpHandler(AbstractRequestHandler):
    def can_handle(self, hi): return is_intent_name('AMAZON.HelpIntent')(hi)
    def handle(self, hi):
        return keep(hi, 'You can ask: current price, hour average, day-ahead prices, full summary, best time to run appliances, last 24 hours, or is it a good time to use electricity.')

class StopHandler(AbstractRequestHandler):
    def can_handle(self, hi):
        return is_intent_name('AMAZON.StopIntent')(hi) or is_intent_name('AMAZON.CancelIntent')(hi)
    def handle(self, hi):
        return end(hi, 'Goodbye! Shift your energy use to low-price hours and save on your bill.')

class SessionEndedHandler(AbstractRequestHandler):
    def can_handle(self, hi): return is_request_type('SessionEndedRequest')(hi)
    def handle(self, hi):    return hi.response_builder.response

class CatchAll(AbstractExceptionHandler):
    def can_handle(self, hi, exc): return True
    def handle(self, hi, exc):
        logging.error('Error: %s', exc, exc_info=True)
        return end(hi, 'Sorry, something went wrong. Please try again.')

for h in [LaunchHandler, FiveMinuteHandler, HourAverageHandler, DayAheadHandler,
          SummaryHandler, BestTimeHandler, Last24Handler, GoodTimeHandler,
          HelpHandler, StopHandler, SessionEndedHandler]:
    sb.add_request_handler(h())
sb.add_exception_handler(CatchAll())
_skill = sb.create()

@app.route('/alexa', methods=['POST'])
def alexa():
    body     = flask_request.data.decode('utf-8')
    envelope = SER.deserialize(body, RequestEnvelope)
    response = _skill.invoke(request_envelope=envelope, context=None)
    return app.response_class(response=SER.serialize(response), status=200, mimetype='application/json')

@app.route('/', methods=['GET'])
def health():
    return '{"status":"ComEd Alexa skill is running"}', 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

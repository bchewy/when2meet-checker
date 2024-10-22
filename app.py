from flask import Flask, render_template, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect
from wtforms import TextAreaField, StringField, validators
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from fuzzywuzzy import fuzz
import time
import logging
import json
import traceback

app = Flask(__name__)
app.config["SECRET_KEY"] = "your-secret-key-here"  # Change this to a random secret key
csrf = CSRFProtect(app)
limiter = Limiter(app, key_func=get_remote_address)

logging.basicConfig(level=logging.INFO)


class ComparisonForm(FlaskForm):
    names_list = TextAreaField(
        "Names List",
        validators=[validators.DataRequired(), validators.Length(max=1000)],
    )
    when2meet_url = StringField(
        "When2Meet URL", validators=[validators.DataRequired(), validators.URL()]
    )


def get_participant_names(url):
    options = Options()
    options.add_argument("-headless")
    logging.info("Starting Firefox in headless mode")
    driver = webdriver.Firefox(options=options)

    try:
        logging.info(f"Navigating to URL: {url}")
        driver.get(url)
        logging.info("Waiting for page to load")

        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        logging.info(f"Page title: {driver.title}")
        logging.info(f"Page URL: {driver.current_url}")

        # Use JavaScript to extract participant names and their availability
        script = """
        var PeopleNames = [];
        var PeopleIDs = [];
        var AvailableAtSlot = [];
        if (typeof window.PeopleNames !== 'undefined' && 
            typeof window.PeopleIDs !== 'undefined' && 
            typeof window.AvailableAtSlot !== 'undefined') {
            PeopleNames = window.PeopleNames;
            PeopleIDs = window.PeopleIDs;
            AvailableAtSlot = window.AvailableAtSlot;
        } else {
            console.log("One or more required variables are not defined");
        }
        return JSON.stringify({PeopleNames: PeopleNames, PeopleIDs: PeopleIDs, AvailableAtSlot: AvailableAtSlot});
        """
        result = json.loads(driver.execute_script(script))

        people_names = result["PeopleNames"]
        people_ids = result["PeopleIDs"]
        available_at_slot = result["AvailableAtSlot"]

        # Create a set of IDs for people who have specified availability
        available_ids = set()
        for slot in available_at_slot:
            available_ids.update(slot)

        # Filter out names of people who haven't specified any availability
        participant_names = [
            name for name, id in zip(people_names, people_ids) if id in available_ids
        ]

        logging.info(
            f"Extracted {len(participant_names)} participant names with availability"
        )
        logging.info(f"Participant names: {participant_names}")

        return participant_names
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")
        logging.error(f"Traceback: {traceback.format_exc()}")
        return []
    finally:
        logging.info("Closing the browser")
        driver.quit()


def find_best_match(name, participant_names, threshold=90):
    best_match = None
    best_ratio = 0
    for participant in participant_names:
        ratio = fuzz.ratio(name.lower(), participant.lower())
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = participant

    if best_ratio >= threshold:
        return best_match
    return None


@app.route("/", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def index():
    form = ComparisonForm()
    if request.method == "POST" and form.validate_on_submit():
        names_list = [
            name.strip() for name in form.names_list.data.split("\n") if name.strip()
        ]
        when2meet_url = form.when2meet_url.data

        logging.info(f"Received POST request with URL: {when2meet_url}")
        logging.info(f"User provided {len(names_list)} names")

        try:
            # Get participant names using Selenium
            participant_names = get_participant_names(when2meet_url)

            logging.info(f"Scraped {len(participant_names)} names from When2Meet")
            logging.info(f"Participant names: {participant_names}")

            # Compare names
            comparison = []
            missing_names = []
            for name in names_list:
                best_match = find_best_match(name, participant_names)
                if best_match:
                    comparison.append((name, best_match))
                    participant_names.remove(best_match)
                else:
                    comparison.append((name, ""))
                    missing_names.append(name)

            # Add any remaining When2Meet names
            for w2m_name in participant_names:
                comparison.append(("", w2m_name))

            logging.info(f"Found {len(missing_names)} missing names")

            return render_template(
                "index.html",
                form=form,
                comparison=comparison,
                missing_names=missing_names,
            )
        except Exception as e:
            logging.error(f"An error occurred: {str(e)}")
            return render_template(
                "index.html",
                form=form,
                error="An error occurred while processing your request. Please try again later.",
            )

    return render_template("index.html", form=form)


@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify(error="Rate limit exceeded. Please try again later."), 429


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

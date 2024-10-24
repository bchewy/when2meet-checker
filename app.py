from flask import Flask, render_template, request, session, jsonify
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
import sys
from datetime import datetime
import pytz

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

        # Modified JavaScript to properly extract data
        script = """
        var result = {
            PeopleNames: [],
            PeopleIDs: [],
            AvailableAtSlot: [],
            TimeZone: ''
        };

        // Debug logging
        console.log('Window variables:', {
            hasPeopleNames: typeof window.PeopleNames !== 'undefined',
            hasPeopleIDs: typeof window.PeopleIDs !== 'undefined',
            hasAvailableAtSlot: typeof window.AvailableAtSlot !== 'undefined'
        });

        // Properly assign window variables to result
        if (typeof window.PeopleNames !== 'undefined') {
            result.PeopleNames = window.PeopleNames;
        }
        if (typeof window.PeopleIDs !== 'undefined') {
            result.PeopleIDs = window.PeopleIDs;
        }
        if (typeof window.AvailableAtSlot !== 'undefined') {
            result.AvailableAtSlot = window.AvailableAtSlot;
        }

        // Get timezone
        var tzSelect = document.getElementById('ParticipantTimeZone');
        if (tzSelect) {
            result.TimeZone = tzSelect.options[tzSelect.selectedIndex].text;
        }

        // Debug logging
        console.log('Extracted data:', result);
        
        return JSON.stringify(result);
        """
        
        # Execute script and get result
        result = json.loads(driver.execute_script(script))
        
        logging.info("Raw data from When2Meet:")
        logging.info(f"People Names: {result['PeopleNames']}")
        logging.info(f"People IDs: {result['PeopleIDs']}")
        logging.info(f"Number of availability slots: {len(result['AvailableAtSlot'])}")
        logging.info(f"Timezone: {result['TimeZone']}")

        # Create a set of IDs for people who have specified availability
        available_ids = set()
        for slot in result["AvailableAtSlot"]:
            available_ids.update(slot)

        # Filter out names of people who haven't specified any availability
        participant_names = [
            name for name, id in zip(result["PeopleNames"], result["PeopleIDs"]) 
            if id in available_ids
        ]

        logging.info(f"Extracted {len(participant_names)} participant names with availability")
        logging.info(f"Participant names: {participant_names}")

        # Add this after the initial page load wait
        WebDriverWait(driver, 20).until(
            lambda d: d.execute_script(
                "return typeof window.PeopleNames !== 'undefined' && "
                "typeof window.PeopleIDs !== 'undefined' && "
                "typeof window.AvailableAtSlot !== 'undefined'"
            )
        )

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
@limiter.limit("50 per minute")
def index():
    form = ComparisonForm()
    
    # Default values for the template
    context = {
        'form': form,
        'time_slots': None,
        'comparison': None,
        'missing_names': None,
        'best_slots': None,
        'continuous_slots': None,
        'timezone': None,
        'availability_stats': None
    }
    
    if request.method == "POST" and form.validate_on_submit():
        names_list = [name.strip() for name in form.names_list.data.split("\n") if name.strip()]
        when2meet_url = form.when2meet_url.data
        
        logging.info(f"Received POST request with URL: {when2meet_url}")
        logging.info(f"User provided {len(names_list)} names")
        
        try:
            # Get participant names first
            participant_names = get_participant_names(when2meet_url)
            if not participant_names:
                participant_names = []
            
            # Get time slots and availability data
            analysis = get_participant_data(when2meet_url)
            
            # Find missing names
            missing_names = []
            comparison = []
            
            for name in names_list:
                best_match = find_best_match(name, participant_names)
                if best_match:
                    comparison.append((name, best_match))
                else:
                    comparison.append((name, ""))
                    missing_names.append(name)
            
            # Add any remaining When2Meet names
            for w2m_name in participant_names:
                if not any(match == w2m_name for _, match in comparison):
                    comparison.append(("", w2m_name))
            
            if analysis:
                # Update context with actual values
                context.update({
                    'comparison': comparison,
                    'missing_names': missing_names,
                    'time_slots': analysis.get('time_slots'),
                    'best_slots': analysis.get('best_slots', []),
                    'continuous_slots': analysis.get('continuous_slots', {}),
                    'timezone': analysis.get('timezone', 'Asia/Singapore'),
                    'availability_stats': {
                        'total_slots': analysis.get('total_slots', 0),
                        'max_availability': analysis.get('max_availability', 0),
                        'avg_availability': analysis.get('avg_availability', 0)
                    }
                })
                return render_template("index.html", **context)
            else:
                return render_template(
                    "index.html",
                    form=form,
                    comparison=comparison,
                    missing_names=missing_names,
                    error="Could not extract time slots from When2Meet."
                )
                
        except Exception as e:
            logging.error(f"An error occurred: {str(e)}")
            logging.error(traceback.format_exc())
            return render_template(
                "index.html",
                form=form,
                error="An error occurred while processing your request."
            )

    return render_template("index.html", **context)


@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify(error="Rate limit exceeded. Please try again later."), 429


@app.route('/get_previous_submission/<timestamp>')
def get_previous_submission(timestamp):
    previous_submissions = session.get('previous_submissions', [])
    for submission in previous_submissions:
        if submission['timestamp'] == timestamp:
            return jsonify(submission)
    return jsonify({}), 404


def get_participant_data(url):
    options = Options()
    options.add_argument("-headless")
    driver = webdriver.Firefox(options=options)

    try:
        driver.get(url)
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        script = """
        function formatTime(timestamp) {
            // Force Singapore timezone (UTC+8)
            var date = new Date(timestamp * 1000);
            date = new Date(date.getTime() + (8 * 60 * 60 * 1000));  // Add 8 hours for SGT
            
            var hours = date.getUTCHours();
            var minutes = date.getUTCMinutes();
            var ampm = hours >= 12 ? 'PM' : 'AM';
            hours = hours % 12;
            hours = hours ? hours : 12;
            minutes = minutes < 10 ? '0' + minutes : minutes;
            
            return hours + ':' + minutes + ' ' + ampm;
        }

        function formatDate(timestamp) {
            // Force Singapore timezone (UTC+8)
            var date = new Date(timestamp * 1000);
            date = new Date(date.getTime() + (8 * 60 * 60 * 1000));  // Add 8 hours for SGT
            
            var days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
            var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
            return days[date.getUTCDay()] + ', ' + months[date.getUTCMonth()] + ' ' + date.getUTCDate();
        }

        var result = {
            PeopleNames: window.PeopleNames || [],
            PeopleIDs: window.PeopleIDs || [],
            AvailableAtSlot: window.AvailableAtSlot || [],
            TimeOfSlot: window.TimeOfSlot || [],
            TimeZone: 'Asia/Singapore'  // Force Singapore timezone
        };

        var timeSlots = [];
        for (var i = 0; i < result.TimeOfSlot.length; i++) {
            timeSlots.push({
                timestamp: result.TimeOfSlot[i],
                time: formatTime(result.TimeOfSlot[i]),
                date: formatDate(result.TimeOfSlot[i]),
                available: result.AvailableAtSlot[i] || []
            });
        }

        result.TimeSlots = timeSlots;
        return JSON.stringify(result);
        """

        result = json.loads(driver.execute_script(script))
        
        # Get all participant names for comparison
        all_participants = set(result["PeopleNames"])
        
        # Process time slots
        time_slots = []
        total_participants = len(result["PeopleIDs"])
        
        for slot in result["TimeSlots"]:
            available_people = [
                result["PeopleNames"][result["PeopleIDs"].index(pid)]
                for pid in slot["available"]
            ]
            
            # Calculate availability percentage
            availability_percentage = (len(available_people) / len(result["PeopleIDs"])) * 100
            
            time_slot = {
                "time": slot["time"],
                "date": slot["date"],
                "available_people": available_people,
                "num_available": len(available_people),
                "timestamp": slot["timestamp"],
                "availability_percentage": availability_percentage
            }
            time_slots.append(time_slot)

        logging.info(f"Extracted {len(time_slots)} time slots with availability")
        
        # Find best time slots (where most people are available)
        best_slots = []
        current_date = None
        current_date_slots = []
        
        for slot in sorted(time_slots, key=lambda x: x["timestamp"]):
            if slot["availability_percentage"] >= 70:  # At least 70% of people available
                if current_date != slot["date"]:
                    if current_date_slots:
                        best_slots.extend(current_date_slots)
                    current_date = slot["date"]
                    current_date_slots = []
                current_date_slots.append(slot)
        
        if current_date_slots:
            best_slots.extend(current_date_slots)

        # Sort best slots by availability percentage
        best_slots.sort(key=lambda x: (-x["availability_percentage"], x["timestamp"]))
        
        # Add this before creating the analysis dict
        if time_slots:
            max_availability = max(slot["num_available"] for slot in time_slots)
            avg_availability = sum(slot["num_available"] for slot in time_slots) / len(time_slots)
        else:
            max_availability = 0
            avg_availability = 0

        # Find continuous time blocks (1hr, 2hr, 3hr)
        def find_continuous_slots(slots, required_slots, min_availability_pct=50):
            continuous_blocks = []
            current_block = []

            # Sort slots by timestamp
            sorted_slots = sorted(slots, key=lambda x: x["timestamp"])

            for i, slot in enumerate(sorted_slots):
                if slot["availability_percentage"] >= min_availability_pct:
                    if not current_block:
                        current_block = [slot]
                    else:
                        # Check if this slot is within 15 minutes of the last one
                        last_timestamp = current_block[-1]["timestamp"]
                        if slot["timestamp"] - last_timestamp == 900:  # 900 seconds = 15 minutes
                            current_block.append(slot)
                        else:
                            # Gap found, check if previous block meets requirements
                            if len(current_block) >= required_slots:
                                continuous_blocks.append(current_block)
                            current_block = [slot]

                    # Check if we have enough slots for the required duration
                    if len(current_block) >= required_slots:
                        continuous_blocks.append(current_block[:])
                        current_block = current_block[1:]  # Slide window by 1
                else:
                    if len(current_block) >= required_slots:
                        continuous_blocks.append(current_block)
                    current_block = []

            # Check final block
            if len(current_block) >= required_slots:
                continuous_blocks.append(current_block)

            # If no blocks found with current threshold, try with lower thresholds
            if not continuous_blocks:
                for threshold in [40, 30, 20]:  # Try progressively lower thresholds
                    continuous_blocks = []
                    current_block = []
                    for i, slot in enumerate(sorted_slots):
                        if slot["availability_percentage"] >= threshold:
                            if not current_block:
                                current_block = [slot]
                            else:
                                last_timestamp = current_block[-1]["timestamp"]
                                if slot["timestamp"] - last_timestamp == 900:
                                    current_block.append(slot)
                                else:
                                    if len(current_block) >= required_slots:
                                        continuous_blocks.append(current_block)
                                    current_block = [slot]

                            if len(current_block) >= required_slots:
                                continuous_blocks.append(current_block[:])
                                current_block = current_block[1:]
                        else:
                            if len(current_block) >= required_slots:
                                continuous_blocks.append(current_block)
                            current_block = []

                    if len(current_block) >= required_slots:
                        continuous_blocks.append(current_block)

                    if continuous_blocks:
                        break

            return continuous_blocks

        # Find blocks for different durations
        one_hour_blocks = find_continuous_slots(time_slots, 4)  # 4 x 15min = 1hr
        two_hour_blocks = find_continuous_slots(time_slots, 8)  # 8 x 15min = 2hr
        three_hour_blocks = find_continuous_slots(time_slots, 12)  # 12 x 15min = 3hr

        # Process blocks into a more usable format
        def process_blocks(blocks):
            processed = []
            for block in blocks:
                avg_availability = sum(s["num_available"] for s in block) / len(block)
                avg_percentage = sum(s["availability_percentage"] for s in block) / len(block)
                
                # Get people available for the entire duration
                common_people = list(set.intersection(*[set(s["available_people"]) for s in block]))
                
                # Get people available for at least 75% of the duration
                min_slots_required = int(len(block) * 0.75)
                frequently_available = []
                all_people = set()
                for s in block:
                    all_people.update(s["available_people"])
                
                for person in all_people:
                    available_count = sum(1 for s in block if person in s["available_people"])
                    if available_count >= min_slots_required:
                        frequently_available.append(person)
                
                processed.append({
                    "start_time": block[0]["time"],
                    "end_time": block[-1]["time"],
                    "date": block[0]["date"],
                    "avg_available": round(avg_availability, 1),
                    "avg_percentage": round(avg_percentage, 1),
                    "available_people": common_people if common_people else frequently_available,
                    "duration_minutes": len(block) * 15
                })
            return sorted(processed, key=lambda x: (-x["avg_percentage"], x["date"], x["start_time"]))[:5]

        continuous_slots = {
            "one_hour": process_blocks(one_hour_blocks),
            "two_hour": process_blocks(two_hour_blocks),
            "three_hour": process_blocks(three_hour_blocks)
        }

        analysis = {
            "time_slots": time_slots,
            "best_slots": best_slots[:5],
            "continuous_slots": continuous_slots,  # Add this new field
            "total_slots": len(time_slots),
            "timezone": "Asia/Singapore",
            "max_availability": max_availability,
            "avg_availability": avg_availability,
            "all_participants": list(all_participants)
        }

        # In get_participant_data function, after processing the blocks:
        logging.info(f"Generated continuous slots:")
        logging.info(f"One hour blocks: {len(continuous_slots['one_hour'])}")
        logging.info(f"Two hour blocks: {len(continuous_slots['two_hour'])}")
        logging.info(f"Three hour blocks: {len(continuous_slots['three_hour'])}")

        return analysis

    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")
        logging.error(traceback.format_exc())
        return None
    finally:
        driver.quit()


def process_when2meet_data(driver, names_list):
    # ... existing code ...
    
    # Convert timezone to Singapore
    sg_timezone = pytz.timezone('Asia/Singapore')
    
    # When processing time slots
    time_slots = []
    for date_str, times in grouped_slots.items():
        for time_str, people in times.items():
            # Convert the datetime to Singapore time
            dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            sg_dt = pytz.utc.localize(dt).astimezone(sg_timezone)
            
            time_slots.append({
                'date': sg_dt.strftime("%Y-%m-%d"),
                'time': sg_dt.strftime("%I:%M %p"),  # 12-hour format with AM/PM
                'num_available': len(people),
                'available_people': sorted(people)
            })
    
    # Sort time slots by number of available people (descending)
    time_slots.sort(key=lambda x: (-x['num_available'], x['date'], x['time']))
    
    # Get the best slots (slots with maximum availability)
    max_availability = round(max(slot['num_available'] for slot in time_slots) if time_slots else 0, 2)
    best_slots = [slot for slot in time_slots if slot['num_available'] == max_availability]
    
    # Filter for reasonable meeting times (e.g., 9 AM to 6 PM)
    business_hours_slots = [
        slot for slot in best_slots
        if 9 <= int(datetime.strptime(slot['time'], "%I:%M %p").strftime("%H")) <= 18
    ]
    
    # Use business hours slots if available, otherwise use all best slots
    recommended_slots = business_hours_slots if business_hours_slots else best_slots
    recommended_slots = recommended_slots[:5]  # Top 5 recommendations
    
    logging.info(f"Max availability: {max_availability}")
    return {
        'time_slots': time_slots,
        'best_slots': recommended_slots,
        'availability_stats': {
            'total_slots': len(time_slots),
            'max_availability': max_availability,
            'avg_availability': sum(slot['num_available'] for slot in time_slots) / len(time_slots) if time_slots else 0
        }
    }

if __name__ == '__main__':
    app.run(debug=True)


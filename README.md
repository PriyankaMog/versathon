OutbreakRadar

Community Health Early-Warning Platform

Problem Statement: H3 — Community Health & Disease Alert Map

Live Deployment: https://versathon.onrender.com/

OutbreakRadar is a community health early-warning platform that collects health-related reports from citizens and health workers, visualizes local activity, identifies unusual patterns, explains the evidence behind an alert, and gives authorized health authorities a workflow to investigate and respond.

The system is designed for early warning and awareness. It does not diagnose individuals and does not claim to confirm a disease outbreak.

The Problem

Community health information can be scattered across many individual reports. A single report may not show a meaningful pattern, but several reports from the same area over a short period can indicate unusual local activity.

The main challenge is to turn these scattered signals into information that a health authority can understand and investigate while reducing misuse, protecting location privacy, and keeping a human decision-maker in the loop.

Our Solution

OutbreakRadar follows a complete workflow:
Report → Map → Detect → Explain → Act → Inform
Citizens and health workers submit symptom reports.
Reports are associated with a ward and stored in the backend.
The system compares current activity with the normal level for that ward.
Recent reports are checked for geographic clustering.
The system considers independent reporters and verified health-worker evidence.
An explainable alert strength score is calculated.
Authorities can investigate, mark a false alarm, confirm, or escalate an alert.
Authorities can publish a public advisory when appropriate.
An alert is treated as an early-warning signal requiring verification, not as proof of an outbreak.

Main Features

Community Reporting
Citizen and health-worker accounts
Symptom categories: fever, diarrhoea, cough, rash and other
Severity selection: low, medium and high
Ward-based reporting
Optional location input
Current-location support for finding the relevant ward
Community Map
Ward-level health status
Normal, Watch, Rising and Critical status levels
Geographic visualization using an interactive map
Heat and trend views for recent activity
Public view does not expose individual exact report locations
Detection Engine
The detection engine uses transparent rules rather than a black-box prediction model.


The prototype Alert Strength Score uses four components:

40% — Local baseline: compares current activity with the ward's normal level.
30% — Spatial clustering: checks how concentrated recent reports are within a configurable 500 metre prototype radius.
20% — Health-worker evidence: verified health-worker reports provide stronger supporting evidence.
10% — Independent reporters: checks how many distinct reporting accounts contribute to the signal.

Status thresholds:

0–24: Normal
25–44: Watch
45–69: Rising
70–100: Critical

The weights and thresholds are prototype engineering choices. They are not a clinically or epidemiologically validated risk model.

Explainable Alerts
Each alert shows the evidence contributing to the signal, including:
Current activity
Local baseline
Spatial cluster information
Independent reporters
Health-worker evidence
Alert strength score
Alert status
Alert history
This allows an authority to understand why a signal was raised instead of receiving only an unexplained warning.


Health-Worker Verification
Citizen reports are treated as unverified early signals.

Health-worker accounts can be verified by an authorized authority. Verified health-worker reports provide stronger supporting evidence and can represent multiple field observations, subject to system limits.

Verification strengthens the evidence but does not automatically confirm an outbreak. The authority remains responsible for investigation and final action.


Protection Against Misuse

A major design goal is to prevent one account from manufacturing strong community evidence through repeated submissions.
Account Controls
Reports are linked to user accounts.
Citizen reporting is rate-limited.
Duplicate submissions for the same symptom and ward within the restricted time window are rejected.
Verified health workers have a higher reporting limit for legitimate field observations.
Failed sign-ins are temporarily locked after repeated failures.


Independent-Evidence Check

The system counts independent citizen accounts separately. A large number of reports from one account should not be treated as equivalent to reports from many independent people.

Suspicious reporting patterns can be flagged, trust can be reduced, and authority users can suspend, reinstate or ban accounts.

A single citizen report cannot be automatically proven true or false. OutbreakRadar therefore uses multiple signals, account controls, verification and authority investigation instead of claiming perfect fake-report detection.


Authority Dashboard

Authorized health authorities have a separate view for investigation and moderation.

Authorities can:

Monitor ward activity and active alerts
View alert evidence and score components
Review reports and user accounts
Verify health-worker accounts
Mark reports as false
Suspend, reinstate or ban accounts
Mark alerts as Needs Investigation
Mark an alert as False Alarm
Confirm an alert through the authority workflow
Escalate an alert
Publish public health advisories
Review audit history
The final public-health decision remains with the responsible authority.


Privacy

Exact report coordinates can be used by the backend for geographic calculations, but public map responses are kept at the ward level rather than exposing individual report locations.

The application also supports location-based ward selection without treating an individual's exact location as public information.


Working Model / Demo Simulation

The project includes a presentation simulator for demonstrating the detection workflow quickly.

The simulator can move the demonstration through increasing activity levels:

Normal → Watch → Rising → Critical

It can also demonstrate a spam scenario where many reports come from a small number of accounts.

Simulated reports are labelled as simulated and are processed through the same detection logic used for incoming reports. The simulator is only for demonstration and does not represent real outbreak data.

To enable the simulator locally:

OR_DEMO=1 uvicorn main:app

Then sign in as the authority and open the Simulator section.


System Architecture

Citizens / Health Workers
          ↓
     Web Interface
 HTML / CSS / JavaScript
 Leaflet / Charts
          ↓
   FastAPI + Uvicorn
          ↓
   Detection Engine
 Baseline + Spatial Cluster
 Independent Evidence
 Health-Worker Evidence
          ↓
       SQLite
 Reports / Users / Alerts
 Audit / Advisories
          ↓
   Authority Dashboard
 Investigation / Moderation
          ↓
     Public Advisory



Technology Stack

Frontend

HTML
CSS
JavaScript
Leaflet.js
OpenStreetMap
Chart.js

Backend
Python
FastAPI
Uvicorn

Database
SQLite


Detection

Python rule-based calculations
Local baseline comparison
Haversine geographic distance calculation
Spatial clustering
Independent-reporter analysis
Health-worker evidence

Deployment
Render


HTTPS public deployment

Live application: https://versathon.onrender.com/

Why This Technology Stack

FastAPI provides a lightweight Python backend for APIs, authentication, reporting and detection logic.

SQLite is suitable for a 24-hour prototype because it is lightweight, requires no separate database server and is simple to integrate with the backend. A production deployment can move to PostgreSQL for larger-scale storage and concurrency.

Leaflet and OpenStreetMap provide interactive geographic visualization without requiring a heavy frontend framework.

The detection approach is intentionally transparent so that an authority can understand the reason behind a signal.

Local Setup

Install the dependencies:

pip install -r requirements.txt

Run the application:

uvicorn main:app --reload

Open:

http://127.0.0.1:8000

The SQLite database is created automatically on first start.

Application Pages

/ — Landing page

/login.html — Sign in and account creation

/app.html — Community map and reporting dashboard

The authority account can access the authority-specific moderation, advisory, coverage and investigation functions after authentication.

Authority Account Configuration

The authority email can be configured with:

OR_AUTHORITY_EMAIL=your-authority-email
OR_AUTHORITY_PASSWORD=your-authority-password

If a password is not configured, the application generates a password on first start and prints it in the terminal.

Current Prototype Limitations

The 30-day baseline history used by the prototype is synthetic.

Demo wards are placeholder geographic areas and should be replaced with real ward boundaries and validated local data for real deployment.

Alert weights and thresholds are prototype engineering choices, not validated epidemiological measures.

The 500 metre clustering radius is a configurable prototype parameter.

A verified health worker can contribute multiple field observations, so authority verification and investigation remain important.

Authentication and infrastructure would need additional hardening for production deployment.

Real deployment would require appropriate governance, privacy controls, validated health data and integration with authorized public-health workflows.

Future Scope

Real ward boundaries and historical public-health data

PostgreSQL and production-scale infrastructure

Stronger identity and organization verification

Privacy-preserving analytics

Integration with appropriate public-health reporting systems

More advanced anomaly detection when representative and ethically governed datasets are available

Improved authority workflows and notifications

Project Summary

OutbreakRadar converts scattered community health reports into an explainable early-warning workflow.

Report → Map → Detect → Explain → Act → Inform

The system is designed to help authorities see unusual local activity earlier, understand the evidence behind a signal, investigate it, and communicate verified information responsibly.

Live Demo: https://versathon.onrender.com/

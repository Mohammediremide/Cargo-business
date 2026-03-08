# CargoFish Website

A premium logistics website for cargo transport, specializing in fresh fish and perishables.
Built with **Python (Flask)** and **Tailwind CSS**.

## Prerequisites

Since this application uses a Python backend, you need to have Python installed on your machine.

1.  **Install Python**: Download from [python.org](https://www.python.org/downloads/).
2.  **Verify Installation**: Open a terminal and run `python --version`.

## Setup Instructions

1.  Open your terminal/command prompt.
2.  Navigate to the project directory:
    ```bash
    cd cargo_fish_app
    ```
3.  Install the required dependencies:
    ```bash
    pip install -r requirements.txt
    ```

## Running the Application

1.  Start the Flask server:
    ```bash
    python app.py
    ```
2.  Open your web browser and go to:
    ```
    http://127.0.0.1:5000
    ```

## Features

-   **Home Page**: Premium design with "Deep Ocean" theme, services grid, and trust indicators.
-   **Booking System**: Multi-step form to book cargo shipments.
-   **Payment Simulation**: Visual credit card form that simulates processing a payment.

## Project Structure

-   `app.py`: The main Flask application file.
-   `templates/`: HTML files (Base layout, Home, Booking).
-   `static/`: CSS, JavaScript, and images.

# =============================================================================
# Script Name: vast_offer_tester.py
# Description:
#     This Python script automates the process of searching for offers using the
#     VAST tool, filtering unverified offers (when specified), selecting the best
#     offers based on deep learning performance (dlperf), and performing self-tests
#     on the associated machines. The results of these tests are then saved to
#     output files, and a summary of failures is presented.
#
# Dependencies:
#     - subprocess: To execute external commands.
#     - json: To parse JSON data.
#     - concurrent.futures (ThreadPoolExecutor, as_completed): To handle concurrent
#       execution of machine tests.
#     - threading: To manage thread-safe operations.
#     - datetime: To timestamp the results.
#     - tqdm: For progress bars (though not used in the current script).
#     - time & random: To implement retry logic with random backoff.
#     - collections (Counter): To count failure reasons.
#     - tabulate: To format the failure summary table.
#     - argparse: To parse command-line arguments.
#
# Functions:
#
#     run_vast_search(verified, host_id):
#         Executes the VAST search command to retrieve offers based on the provided
#         verification status and host ID. It limits the results to 65,535 offers and
#         includes both verified and unverified offers in raw JSON format. If the
#         command fails, it returns an empty list.
#
#     get_unverified_offers(offers):
#         Filters the list of offers to identify unverified offers whose
#         machine IDs are not present in any verified offers. This ensures that
#         only truly unverified offers are processed.
#
#     get_best_offers(offers):
#         From the list of offers, selects the best offer for each
#         machine based on the highest deep learning performance (dlperf). This
#         results in a dictionary mapping machine IDs to their best offer.
#
#     test_machine(machine_id):
#         Performs a self-test on a given machine by executing the
#         './vast.py self-test machine <machine_id> --raw' command. It includes
#         robust error handling with up to 30 retries in case of rate limiting
#         (HTTP 429 errors). The function returns a tuple containing the machine
#         ID, status ('success' or 'failure'), and a reason for failure if any.
#
#     process_machine_ids(machine_ids):
#         Manages the concurrent execution of self-tests on multiple machines
#         using a ThreadPoolExecutor with a maximum of 10 worker threads. It
#         tracks the number of processed, passed, and failed tests, and updates
#         the user on the progress in real-time. The function returns lists of
#         successful and failed machine IDs.
#
#     save_results(successes, failures):
#         Saves the results of the self-tests to two separate files:
#             - 'passed_machines.txt': Contains a timestamp and a comma-separated
#               list of machine IDs that passed the tests.
#             - 'failed_machines.txt': Contains a timestamp and a list of machine
#               IDs that failed, along with the reasons for failure.
#
#     print_failure_summary(failures):
#         Generates and prints a summary table of failure reasons using the
#         'tabulate' library. If 'tabulate' is not installed, it falls back to
#         basic string formatting. This provides a clear overview of why tests
#         failed across different machines.
#
#     parse_arguments():
#         Parses command-line arguments using argparse.
#
#     main():
#         The main orchestrator function that ties together all other functions.
#         It performs the following steps:
#             1. Parses command-line arguments for verification status and host ID.
#             2. Runs a VAST search to retrieve offers based on the provided filters.
#             3. Conditionally filters to get unverified offers if verification is set to false.
#             4. Selects the best offers based on download performance.
#             5. Processes the machine IDs by performing self-tests.
#             6. Saves the results to output files.
#             7. Prints a summary of the test results.
#
# Execution:
#     When the script is run directly, the 'main()' function is invoked, executing
#     the entire workflow described above.
#
# Error Handling:
#     - The script includes comprehensive error handling for subprocess commands,
#       including retries with exponential backoff in case of rate limiting.
#     - It handles JSON parsing errors and unexpected exceptions gracefully,
#       ensuring that failures are logged with appropriate reasons.
#
# Output:
#     - 'passed_machines.txt': Lists machine IDs that successfully passed the
#       self-tests.
#     - 'failed_machines.txt': Lists machine IDs that failed the self-tests along
#       with the reasons for failure.
#     - Console Output: Provides real-time updates on the progress of tests and
#       a summary of results upon completion.
#
# Usage:
#     Ensure that all dependencies are installed and that the './vast' and
#     './vast.py' commands are available and executable. Run the script using:
#         python3 vast_offer_tester.py [--verified {true,false,any}] [--host_id HOST_ID]
#
#     Options:
#         --verified {true,false,any}   Set the verification status to filter offers.
#                                       Default is 'false'.
#         --host_id HOST_ID            Specify a particular host ID to filter offers.
#                                       Use 'any' for no filtering. Default is 'any'.
#         -h, --help                   Show this help message and exit.
#
# Example:
#     To search for verified offers with host_id 12345:
#         python3 vast_offer_tester.py --verified true --host_id 12345
# 
# Results saved to 'passed_machines.txt' and 'failed_machines.txt'.
# =============================================================================

import subprocess
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from datetime import datetime
from tqdm import tqdm
import time
import random
from collections import Counter
from tabulate import tabulate
import argparse
import sys


def run_vast_search(verified='any', host_id='any'):
    """
    Executes the VAST search command to retrieve offers based on verification status and host ID.

    Parameters:
        verified (str): 'true', 'false', or 'any' to filter offers by verification status.
        host_id (str or int): Specific host ID to filter offers or 'any' for no filtering.

    Returns:
        list: A list of offer dictionaries retrieved from the VAST search.
    """
    # Validate verified parameter
    valid_verified = {'true', 'false', 'any'}
    if verified.lower() not in valid_verified:
        print(f"Invalid value for --verified: '{verified}'. Must be one of {valid_verified}.")
        sys.exit(1)
    
    # Validate host_id parameter
    if host_id != 'any':
        try:
            host_id = int(host_id)
        except ValueError:
            print(f"Invalid value for --host_id: '{host_id}'. Must be an integer or 'any'.")
            sys.exit(1)
    
    # Construct the verification filter
    verified_filter = f"verified={verified.lower()}"
    
    # Construct the host_id filter
    host_id_filter = f"host_id={host_id}" if host_id != 'any' else "host_id=any"
    
    cmd = ['./vast', 'search', 'offers', '--limit', '65535', verified_filter, host_id_filter, '--raw']
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        output = result.stdout
        return json.loads(output)
    except subprocess.CalledProcessError as e:
        print(f"Error running vast search: {e.stderr.strip()}")
        return []
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON output from vast search: {e}")
        return []
    except Exception as e:
        print(f"Unexpected error running vast search: {e}")
        return []


def get_unverified_offers(offers):
    """
    Filters the list of offers to identify unverified offers whose
    machine IDs are not present in any verified offers.

    Parameters:
        offers (list): List of offer dictionaries.

    Returns:
        list: List of unverified offer dictionaries.
    """
    verified_machine_ids = set()
    for offer in offers:
        if offer.get('verification') == 'verified':
            verified_machine_ids.add(offer.get('machine_id'))

    unverified_offers = [
        offer for offer in offers
        if offer.get('verification') == 'unverified' and offer.get('machine_id') not in verified_machine_ids
    ]
    return unverified_offers


def get_best_offers(offers):
    """
    Selects the best offer for each machine based on the highest deep learning performance (dlperf).

    Parameters:
        offers (list): List of offer dictionaries.

    Returns:
        dict: Dictionary mapping machine IDs to their best offer.
    """
    best_offers = {}
    for offer in offers:
        machine_id = offer.get('machine_id')
        dlperf = offer.get('dlperf', 0)
        if machine_id not in best_offers or dlperf > best_offers[machine_id].get('dlperf', 0):
            best_offers[machine_id] = offer
    return best_offers


def test_machine(machine_id):
    """
    Performs a self-test on a given machine.

    Parameters:
        machine_id (int or str): The ID of the machine to test.

    Returns:
        tuple: (machine_id, status, reason)
    """
    cmd = ['./vast.py', 'self-test', 'machine', str(machine_id), '--raw']
    max_retries = 30  # Increased from 3 to 30
    backoff_factor = 1  # Base factor for exponential backoff (optional)

    for attempt in range(1, max_retries + 1):
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            output = result.stdout.strip()
            stderr_output = result.stderr.strip()
            
            # Check for 429 in the stderr output or HTTP response
            if "429" in stderr_output or "Too Many Requests" in stderr_output:
                if attempt < max_retries:
                    wait_time = random.randint(1, 10)  # Random wait between 1 and 10 seconds
                    print(f"429 Too Many Requests for machine {machine_id}. Retrying in {wait_time} seconds... (Attempt {attempt}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                else:
                    return (machine_id, 'failure', "Too Many Requests: 429 error after 30 retries")
            
            # Parse JSON output
            try:
                data = json.loads(output)
            except json.JSONDecodeError:
                # If JSON parsing fails, check if 429 is still present
                if "429" in output or "Too Many Requests" in output:
                    if attempt < max_retries:
                        wait_time = random.randint(1, 10)
                        print(f"429 Too Many Requests in JSON output for machine {machine_id}. Retrying in {wait_time} seconds... (Attempt {attempt}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    else:
                        return (machine_id, 'failure', "Too Many Requests: 429 error after 30 retries (JSON Decode Error)")
                else:
                    error_message = stderr_output or output
                    return (machine_id, 'failure', f"Invalid JSON output or error message: {error_message}")

            if data.get('success'):
                return (machine_id, 'success', '')
            else:
                reason = data.get('reason', 'Unknown reason')
                return (machine_id, 'failure', reason)
        
        except Exception as e:
            # Handle unexpected exceptions
            return (machine_id, 'failure', f"Exception occurred: {e}")
    
    # If all retries are exhausted without success
    return (machine_id, 'failure', "Request failed after 30 retries")


def process_machine_ids(machine_ids):
    """
    Manages the concurrent execution of self-tests on multiple machines.

    Parameters:
        machine_ids (list): List of machine IDs to test.

    Returns:
        tuple: (successes, failures)
            - successes (list): List of machine IDs that passed the tests.
            - failures (list): List of tuples (machine_id, reason) that failed.
    """
    successes = []
    failures = []
    total_machines = len(machine_ids)
    counter = {'processed': 0, 'passed': 0, 'failed': 0}
    lock = threading.Lock()
    
    print(f"Starting self-tests on {total_machines} machine(s)...")
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(test_machine, mid): mid for mid in machine_ids}
        for future in as_completed(futures):
            machine_id, status, reason = future.result()
            with lock:
                counter['processed'] += 1
                remaining = total_machines - counter['processed']
                if status == 'success':
                    successes.append(machine_id)
                    counter['passed'] += 1
                else:
                    failures.append((machine_id, reason))
                    counter['failed'] += 1
                print('\033[2K\033[1G', end='')  # Clear the entire line and move the cursor to the beginning
                print(f"Processed {counter['processed']}/{total_machines} - Passed: {counter['passed']}, Failed: {counter['failed']}, Remaining: {remaining}", end='\r')
    print()  # Move to the next line after progress updates
    return successes, failures


def save_results(successes, failures):
    """
    Saves the results of the self-tests to output files.

    Parameters:
        successes (list): List of machine IDs that passed the tests.
        failures (list): List of tuples (machine_id, reason) that failed.
    """
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Save passed machines
    try:
        with open('passed_machines.txt', 'w') as f:
            f.write(f"{current_time}\n")  # Add the date and time
            f.write(','.join(str(mid) for mid in successes) + "\n")  # Comma-separated machine IDs
    except Exception as e:
        print(f"Error saving passed_machines.txt: {e}")
    
    # Save failed machines
    try:
        with open('failed_machines.txt', 'w') as f:
            f.write(f"{current_time}\n")  # Add the date and time
            for mid, reason in failures:
                f.write(f"{mid}: {reason}\n")  # Machine ID and reason per line
    except Exception as e:
        print(f"Error saving failed_machines.txt: {e}")


def print_failure_summary(failures):
    """
    Generates and prints a summary table of failure reasons.

    Parameters:
        failures (list): List of tuples (machine_id, reason) that failed.
    """
    # Extract only the reasons from the failures list
    reasons = [reason for _, reason in failures]
    
    # Count the occurrences of each failure reason
    failure_counts = Counter(reasons)
    
    # Prepare data for the table
    table_data = []
    for reason, count in failure_counts.items():
        table_data.append([count, reason])
    
    # Sort the table data by count descending
    table_data.sort(key=lambda x: x[0], reverse=True)
    
    # Print the table using tabulate if available
    try:
        print("\nFailed Machines by Error Type:")
        print(tabulate(table_data, headers=["COUNT", "REASON"], tablefmt="plain"))
    except ImportError:
        # If tabulate is not installed, use basic string formatting
        print("\nFailed Machines by Error Type:")
        print(f"{'COUNT':<5} {'REASON'}")
        print("-" * 60)
        for count, reason in table_data:
            print(f"{count:<5} {reason}")


def parse_arguments():
    """
    Parses command-line arguments.

    Returns:
        argparse.Namespace: Parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Automate searching and testing of VAST offers.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
Example Usage:
    To search for verified offers with a specific host ID:
        python3 vast_offer_tester.py --verified true --host_id 12345

    To search for any verification status and any host ID (default):
        python3 vast_offer_tester.py

    To search for unverified offers regardless of host ID:
        python3 vast_offer_tester.py --verified false

    Results saved to 'passed_machines.txt' and 'failed_machines.txt'.
        """
    )
    parser.add_argument(
        '--verified',
        type=str,
        choices=['true', 'false', 'any'],
        default='false',
        help="Set the verification status to filter offers.\nChoices: 'true', 'false', 'any'.\nDefault: 'false'."
    )
    parser.add_argument(
        '--host_id',
        type=str,
        default='any',
        help="Specify a particular host ID to filter offers or 'any' for no filtering.\nDefault: 'any'."
    )
    return parser.parse_args()


def main():
    """
    The main orchestrator function that ties together all other functions.
    """
    args = parse_arguments()
    
    offers = run_vast_search(verified=args.verified, host_id=args.host_id)
    if not offers:
        print("No offers found or an error occurred during the VAST search.")
        return
    
    # Conditional filtering based on the verified status
    if args.verified.lower() == 'false':
        # When verified is false, clean the list with get_unverified_offers
        unverified_offers = get_unverified_offers(offers)
        offers_to_process = unverified_offers
        print(f"Filtered to {len(unverified_offers)} unverified offer(s) after cleaning.")
    else:
        # When verified is true or any, use all offers without additional filtering
        offers_to_process = offers
        print(f"Using all {len(offers)} offer(s) without additional filtering.")
    
    best_offers = get_best_offers(offers_to_process)
    machine_ids = list(best_offers.keys())

    print(f"Found {len(machine_ids)} machine ID(s) to test.")

    if not machine_ids:
        print("No machines to test.")
        return

    successes, failures = process_machine_ids(machine_ids)
    save_results(successes, failures)

    print(f"\nSummary:")
    print(f"Passed: {len(successes)}")
    print(f"Failed: {len(failures)}")
    
    # Print the failure summary table
    if failures:
        print_failure_summary(failures)
    else:
        print("All machines passed the self-tests.")

    print(f"\nResults saved to 'passed_machines.txt' and 'failed_machines.txt'.")


if __name__ == '__main__':
    main()

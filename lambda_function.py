from logging import fatal
import re
from time import timezone
import boto3
import json
import datetime
from botocore import exceptions
import dateutil
from dateutil.parser import parse
from pprint import pprint
import os
import random
import string
from input_concatenators import *
from utils import *

RULE_PREFIX = os.getenv('RULE_PREFIX', 'AUTO_')
LAMBDA_FUNCTION_NAME_TO_ARN_MAPPING_PREFIX = os.getenv(
    'LAMBDA_FUNCTION_NAME_TO_ARN_MAPPING_PREFIX', 'LAMBDA_')
ALLOWED_T_MINUS_MINUTES = os.getenv('ALLOWED_T_MINUS_MINUTES', False)
# TODO: default concurrent
RULE_TARGET_ADDING_STRATEGY = os.getenv(
    'RULE_TARGET_ADDING_STRATEGY', 'CONCURRENT_LAMBDA_TARGETS')  # INPUT_CONCATENATOR
INPUT_CONCATENATOR_MODULE_NAME = os.getenv(
    'INPUT_CONCATENATOR_MODULE_NAME', 'input_concatenators')
INPUT_CONCATENATOR_CLASS_NAME = os.getenv(
    'INPUT_CONCATENATOR_CLASS_NAME', False)  # EventBridgeSingleArrayInput
REQUIRED_EVENT_INPUTS = ('datetime_utc', 'data', 'lambda_function')


class EventBridgeException(Exception):
    pass


class LambdaSchedulerException(Exception):
    pass


class EventBridge:
    def __init__(self, client=None) -> None:
        # TODO: check the client type to be 'events'
        self.client = client if client else boto3.client('events')
        self._rules = []  # cache

    def list_rules(self, refresh=False, next_token=None, name_prefix=None):
        """ calls itself recursively for SDK pagination """
        if refresh is False and self._rules != []:
            return self._rules  # caching

        all_rules = []
        response = None
        list_rules_kwargs = {}
        if next_token:
            list_rules_kwargs['NextToken'] = next_token
        if name_prefix:
            list_rules_kwargs['NamePrefix'] = name_prefix

        response = self.client.list_rules(
            **list_rules_kwargs
        )

        if not self.is_boto3_response_successful(response):
            raise EventBridgeException(
                "Can't list the existing EventBridge Rules.")

        # pagination of the SDK
        response_next_token = response.get('NextToken', False)
        if response_next_token:
            next_rules = self.list_rules(
                refresh=True, next_token=response_next_token, name_prefix=name_prefix)
            all_rules.extend(next_rules)

        all_rules.extend(response.get('Rules'))
        return all_rules

    @staticmethod
    def is_boto3_response_successful(response):
        http_status_code = response.get(
            'ResponseMetadata', {}).get('HTTPStatusCode', 400)
        return 200 <= http_status_code < 300

    def create_rule_target_concurrent_calls(self, rule_name, lambda_function_arn, data):
        random_postfix = ''.join(random.choice(
            string.ascii_lowercase) for i in range(6))
        target_id = f"{rule_name}-target-{random_postfix}"

        response = self.client.put_targets(
            Rule=rule_name,
            EventBusName='default',
            Targets=[
                {
                    'Id': target_id,
                    'Arn': lambda_function_arn,
                    'Input': json.dumps(data),
                },
            ]
        )
        success = self.is_boto3_response_successful(response)
        failed_entries = response.get('FailedEntryCount', None)
        failed_entry_count = response.get('FailedEntries', None)
        return {'success': success, 'failed_entry_count': failed_entry_count, 'failed_entries': failed_entries}

    def create_rule_target_input_concat(self, rule_name, target, new_data):
        """updates the EventBridgeRule(rule_name)s target data with the new data.
        the logic of the json data updation can be customized with impementing
        your own input_concatenators.EventBridgeInputConcatenator abstract class.
        Also you need to set the environment variable: INPUT_CONCATENATOR_MODULE_NAME 
        and INPUT_CONCATENATOR_CLASS_NAME to be your module and class name."""
        input_class_obj: EventBridgeInputConcatenator = None
        if not INPUT_CONCATENATOR_CLASS_NAME:
            raise EventBridgeException(
                f'Please implement your subclass and update the environment variable: INPUT_CONCATENATOR_CLASS_NAME')
        try:
            # python magic to grab the non-imported class in runtime with module and class name.
            input_concat_class = get_class_by_name_and_module(
                INPUT_CONCATENATOR_MODULE_NAME, INPUT_CONCATENATOR_CLASS_NAME)
            input_class_obj = input_concat_class()
        except:
            raise EventBridgeException(
                f'Couldnt import class {INPUT_CONCATENATOR_MODULE_NAME}.{INPUT_CONCATENATOR_CLASS_NAME}. Please implement your subclass and update the environment variable: INPUT_CONCATENATOR_CLASS_NAME')

        target_lambda_arn = target.get('Arn')
        target_id = target.get('Id')
        target_input = json.loads(target.get('Input', {}))
        # get the combined data and save the target
        combined_data = input_class_obj.concatenate_inputs(
            target_input, new_data)

        response = self.client.put_targets(
            Rule=rule_name,
            EventBusName='default',
            Targets=[
                {
                    'Id': target_id,
                    'Arn': target_lambda_arn,
                    'Input': json.dumps(combined_data),
                },
            ]
        )
        success = self.is_boto3_response_successful(response)
        return {'success': success}

    def create_rule_target(self, rule_name, lambda_function_arn, data):
        rule_name = prefix_the_rule_name(rule_name)

        response = self.get_rules_targets(rule_name)
        if not response.get('success', False):
            raise EventBridgeException(
                f"Can't list rule targets for the rule: {rule_name}")

        existing_rule_targets = response.get('targets')

        if RULE_TARGET_ADDING_STRATEGY == 'CONCURRENT_LAMBDA_TARGETS' \
                and len(existing_rule_targets) < 5:
            result = self.create_rule_target_concurrent_calls(
                rule_name, lambda_function_arn, data)
            return result
        elif len(existing_rule_targets) >= 5:
            raise EventBridgeException(
                f"Max. allowed rule target count is 5. Can't add a new rule target. Please implement env(RULE_TARGET_ADDING_STRATEGY,'INPUT_CONCATENATOR'). {lambda_function_arn} {data}")

        if RULE_TARGET_ADDING_STRATEGY == 'INPUT_CONCATENATOR':
            # select the rule target with the same lambda
            if len(existing_rule_targets) == 0:
                result = self.create_rule_target_concurrent_calls(
                    rule_name, lambda_function_arn, data)
                return result
            selected_ert = None
            for ert in existing_rule_targets:
                if ert.get('Arn', False) == lambda_function_arn:
                    selected_ert = ert
                    break

            if selected_ert is None:
                # create concurrent bc there is no matching lambda.
                result = self.create_rule_target_concurrent_calls(
                    rule_name, lambda_function_arn, data)
                return result
            else:
                # update the data
                result = self.create_rule_target_input_concat(
                    rule_name, target=selected_ert, new_data=data)
                return result

        return {"success": False}

    def create_rule(self, rule_name, date, state='ENABLED', event_bus_name="default"):
        # date = datetime.datetime.now(tz=dateutil.tz.UTC) # TODO: move this
        cron_expr = self.create_cron_expr_for_date(date)
        rule_name = prefix_the_rule_name(rule_name)
        # check if the rule already exists.

        rules = self.list_rules(name_prefix=get_rule_prefix())
        response = None
        success = False
        created_rule_arn = False
        for rule in rules:
            if rule.get('Name') == rule_name:
                response = rule
                success = True
                created_rule_arn = response.get('Arn', False)
                break

        if response is None:
            response = self.client.put_rule(
                Name=rule_name,
                ScheduleExpression=cron_expr,
                State=state,
                Tags=[
                    {
                        'Key': 'auto',
                        'Value': 'true'
                    },
                ],
                EventBusName=event_bus_name
            )

            success = self.is_boto3_response_successful(response)
            created_rule_arn = response.get('RuleArn', False)

        return {'success': success, 'rule_arn': created_rule_arn, 'rule_name': rule_name}

    def get_environment_lambda_name_to_arn_mapping(self):
        prefix = LAMBDA_FUNCTION_NAME_TO_ARN_MAPPING_PREFIX
        mapping = {}
        for key, value in os.environ.items():
            if key.startswith(prefix):
                fn_name = key.lstrip(prefix)
                if value.startswith('arn:'):
                    mapping[fn_name] = value
                else:
                    print(f'Please enter a proper arn. ({key}:{value})')
        return mapping

    def get_from_lambda_name_to_arn_mapping(self, key, default_to=None):
        # if input_lambda_function does not start with 'arn:', we have to look up
        # prefixed lambda function name to arn mappings.

        name_to_arn_map = self.get_environment_lambda_name_to_arn_mapping()
        if name_to_arn_map.get(key, False) == False:
            print('{key} is not a valid ARN. '
                  'You can set custom lambda name to arn mappings using the '
                  f'environment variable prefix {LAMBDA_FUNCTION_NAME_TO_ARN_MAPPING_PREFIX}')
        return name_to_arn_map.get(key, default_to)

    def create_rule_from_event(self, event):
        rule_name = self.generate_rule_name_from_event(event)
        date = event.get('datetime_utc')
        input_lambda_function = event.get('lambda_function')
        data = event.get('data')
        lambda_function_arn = None

        # TODO DELETE THIS
        # return self.create_rule_target('AUTO_2021-7-7--8-53', lambda_function_arn, data)

        # get the arn either from event, or the environment variables prefixed with LAMBDA_FUNCTION_NAME_TO_ARN_MAPPING_PREFIX
        if input_lambda_function.startswith('arn:'):
            lambda_function_arn = input_lambda_function
        else:
            env_lambda_arn = self.get_from_lambda_name_to_arn_mapping(
                input_lambda_function, False)
            if env_lambda_arn:
                lambda_function_arn = env_lambda_arn
            else:
                raise EventBridgeException(
                    f'{input_lambda_function} is not an ARN or mapped in the environment variables.')

        rule_to_create_target_on = None
        # if ALLOWED_T_MINUS_ is enabled, check for the rules within the window
        if ALLOWED_T_MINUS_MINUTES is not False:
            allowed_t_minus_minutes = int(ALLOWED_T_MINUS_MINUTES)
            neartime_rules = self.find_rules_close_to_a_date(
                date, allowed_t_minus_minutes, lambda_function_arn, name_prefix=get_rule_prefix())
            if len(neartime_rules) >= 1:
                rule_to_create_target_on = neartime_rules[0]
                rule_to_create_target_on.update({'success': True})
                rule_name = rule_to_create_target_on.get('Name')
        # create the rule and its targets
        if rule_to_create_target_on is None:
            rule_to_create_target_on = self.create_rule(rule_name, date)
            rule_name = rule_to_create_target_on.get('rule_name')

        if rule_to_create_target_on.get('success', False):

            created_target = self.create_rule_target(
                rule_name, lambda_function_arn, data)
            if created_target.get('success', False):
                return {'success': True}
            else:
                deleted = self.delete_rule(rule_name)

        return {'success': False}

    def create_cron_expr_for_date(self, date):
        day_of_week = '?'
        return f"cron({date.minute} {date.hour} {date.day} {date.month} {day_of_week} {date.year})"

    def decode_cron_expr_to_date(self, cron_expr):
        if cron_expr.startswith('cron('):
            cron_expr = cron_expr.lstrip('cron(').rstrip(')')
        minute, hour, day, month, _, year = cron_expr.split(' ')
        datetime_obj = datetime.datetime(minute=int(minute), hour=int(hour), day=int(
            day), month=int(month), year=int(year), tzinfo=dateutil.tz.UTC)
        return datetime_obj

    # TODO: get the lambda_arn as a param too.
    def find_rules_close_to_a_date(self, date, t_minus_in_minutes, lambda_function_arn, name_prefix=None):
        """ """
        rules = self.list_rules(refresh=True, name_prefix=name_prefix)
        selected_rules = []
        for rule in rules:
            try:
                cron_expr = rule.get('ScheduleExpression')
                cron_datetime = self.decode_cron_expr_to_date(cron_expr)
                rule['cron_datetime'] = cron_datetime

                offset_ago = date - \
                    datetime.timedelta(minutes=t_minus_in_minutes)
                if offset_ago <= cron_datetime <= date:
                    selected_rules.append(rule)
            except:
                # ignore un-supported cron expression exceptions
                pass

        return selected_rules

    def get_rules_targets(self, rule_name, next_token=None, event_bus_name='default') -> bool:
        """recursive"""
        rule_name = prefix_the_rule_name(rule_name)
        output = []  # all rule targets of rule_name
        params = {
            "Rule": rule_name,
            "EventBusName": event_bus_name,
        }

        if next_token:
            params["NextToken"] = next_token
        try:
            response = self.client.list_targets_by_rule(**params)
        except Exception as e:
            return {'success': False, 'exception': e}

        targets = response.get('Targets', [])
        output.extend(targets)

        response_next_token = response.get('NextToken', False)
        if response_next_token:  # pagination
            next_rules = self.get_rules_targets(
                rule_name, next_token=response_next_token)
            if next_rules.get('success', False):
                output.extend(next_rules.get('targets', []))

        success = self.is_boto3_response_successful(response)
        return {'success': success, 'targets': output}

    def delete_rules_targets(self, rule_name, event_bus_name='default') -> bool:
        rule_name = prefix_the_rule_name(rule_name)
        # delete the targets first.
        rules_targets = self.get_rules_targets(rule_name)
        success = rules_targets.get('success', False)
        if success:
            targets = rules_targets.get('targets', [])
            target_ids = [x.get('Id') for x in targets if x.get('Id', False)]
            if not target_ids:
                return True  # no targets registered to rule

            response = self.client.remove_targets(
                Rule=rule_name,
                EventBusName=event_bus_name,
                Ids=target_ids
            )
            success = success or self.is_boto3_response_successful(response)

        return success

    def delete_rule(self, rule_name, event_bus_name='default'):
        # get the lists
        rule_name = prefix_the_rule_name(rule_name)
        # delete the targets first.
        targets_deleted = self.delete_rules_targets(rule_name, event_bus_name)
        # ignoring deleted targets, there may be no targets in the rule
        success = False
        #  delete the rule.
        try:
            response = self.client.delete_rule(
                Name=rule_name,
                EventBusName=event_bus_name,
            )
            success = self.is_boto3_response_successful(response)

        except Exception as e:
            success = False

        return success

    def clean_up_expired_rules(self):
        """deletes expired Rules created by this Lambda Function."""
        deleted_rules = []
        rules = self.list_rules(name_prefix=get_rule_prefix())
        for rule in rules:
            schedule_expr = rule.get('ScheduleExpression')
            rule_date = self.decode_cron_expr_to_date(schedule_expr)
            rule_name = rule.get('Name')
            now_utc = datetime.datetime.now(tz=dateutil.tz.UTC)
            if now_utc > rule_date:
                is_rule_deleted = self.delete_rule(rule_name)
                if is_rule_deleted:
                    deleted_rules.append(rule)
                else:
                    print(f"Couldn't delete Rule: {rule}")
        return deleted_rules

    def generate_rule_name_from_event(self, event):
        date = event.get('datetime_utc')
        rule_name = f'{date.year}-{date.month}-{date.day}--{date.hour}-{date.minute}'
        rule_name = prefix_the_rule_name(rule_name)
        return rule_name


def get_lambda_scheduler_cron_expression():
    """
    CALL_LAMBDA_SCHEDULER_EVERY_N_MINUTES and CALL_LAMBDA_SCHEDULER_EVERY_N_HOURS env vars are mutualy exclusive
    returns "rate(n minutes)" or "rate(n hours)", defaults to "rate(3 hours)" 
    """
    every_n_minutes = os.getenv('CALL_LAMBDA_SCHEDULER_EVERY_N_MINUTES', None)
    every_n_hours = os.getenv('CALL_LAMBDA_SCHEDULER_EVERY_N_HOURS', 3)
    schedule_expr_type = None
    n = None
    try:
        if every_n_minutes:
            # checking if the value is an integer
            every_n_minutes = int(every_n_minutes)
            schedule_expr_type = 'minutes'
        elif every_n_hours:
            # checking if the value is an integer
            every_n_hours = int(every_n_hours)
            schedule_expr_type = 'hours'
        else:
            raise LambdaSchedulerException(
                "At least one of 'every_n_minutes' or 'every_n_hours' has to be not None.")
    except ValueError:
        raise LambdaSchedulerException("'every_n_minutes' or 'every_n_hours' parameter can't be converted to int. "
                                       "Please properly set one of the following environment variables: "
                                       "[CALL_LAMBDA_SCHEDULER_EVERY_N_MINUTES, CALL_LAMBDA_SCHEDULER_EVERY_N_HOURS]")

    return f"rate({n} {schedule_expr_type})"


def get_rule_prefix():
    return RULE_PREFIX


def create_lambda_schedulers_rule(event_bridge: EventBridge, current_lambda_function_arn):
    """
    try to grab existing rule.
    if it doesn't exist, create it
    if it exists update the rules.
    """
    scheduler_cron_expr = get_lambda_scheduler_cron_expression()
    rules = event_bridge.list_rules(name_prefix=get_rule_prefix())
    pprint(rules)
    pass


def prefix_the_rule_name(rule_name):
    prefix = get_rule_prefix()
    if not prefix:
        raise LambdaSchedulerException(
            "You must set the 'RULE_PREFIX' environment variable.")

    if not rule_name.startswith(prefix):
        return f"{prefix}{rule_name}"
    return rule_name


def validate_event(event):
    def true_if_not_false(x): return True if x != False else False
    input_existence_list = [true_if_not_false(event.get(e_input, False))
                            for e_input in REQUIRED_EVENT_INPUTS]
    has_all_inputs = all(input_existence_list) 
    return has_all_inputs


def lambda_handler(event, context):
    # create the this lambda functions event
    eventbridge = EventBridge(boto3.client('events'))
    # eventbridge = EventBridge() # create with the default client
    try:
        event = json.loads(event)
    except:
        pass


    if not validate_event(event):
        return {'success': False, 'message': f'Please provide all of the parameters: {REQUIRED_EVENT_INPUTS=}'}

    # parse the datetime_utc string to datetime obj
    try:
        event['datetime_utc'] = parse(event['datetime_utc'])
    except Exception as e:
        return {'success': False, 'message': f"datetime_utc parameter can't be parsed."}

    deleted_rules = eventbridge.clean_up_expired_rules()
    pprint({'deleted_expired_rules': deleted_rules})

    try:
        created_rule = eventbridge.create_rule_from_event(event)
        return created_rule
    except Exception as e:
        return {'success': False, 'event': event, 'exception': str(e)}


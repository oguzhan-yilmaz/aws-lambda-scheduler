# aws-lambda-scheduler

`aws-lambda-scheduler` lets you call any existing AWS Lambda Function you have **in the future**.

This functionality is achieved by dynamically managing the EventBridge Rules.

`aws-lambda-scheduler` also has optimizations you can configure and extend yourself. AWS allows maximum of 300 EventBridge rules in a region. If you are expecting to create more than 300 rules, check out **Optimizations** section below.


### Example Usage
When you set up the `aws-lambda-scheduler` in your AWS environment, you can simply call it with a json data like this:
```json
{
    "datetime_utc": "2030-12-30 20:20:20",
    "lambda_function": "arn:aws:lambda:...........",
    "data": {
        "any": "json",
        "is": "allowed"
    }
}
```

`aws-lambda-scheduler` will create a EventBridge rule, and AWS will run the specified `lambda_function` at the `datetime_utc` with the given `data`.

It's that simple. Just remember to convert your datetime to `UTC+0` timezone. That's the timezone supported by EventBridge Rules.

## Installation

1. Create a IAM Role with AWS managed `AmazonEventBridgeFullAccess` and `AWSLambdaBasicExecutionRole` Roles.
2. Create a Lambda Function with Python runtime and attach the role you've created to it.
3. Upload the `aws-lambda-scheduler.zip` file to your Lambda Function.


## How it works

EventBridge Rules are basically cron jobs of AWS. EventBridge Rules must have a `schedule`, `data`, and minimum of one `target` -- in this case the target is a lambda function.

Rules `schedule` can be fixed rate of minutes, or a cron job schedule expression. `aws-lambda-scheduler` takes advantage of cronjob schedule expression and creates a Rule that will run only one time.

#### what happens on runtime
1. `aws-lambda-scheduler` will create a EventBridge Rule with the date of `datetime_utc`, target of `lambda_function` and targets Constant Json Data being `data`.
2. `aws-lambda-scheduler` will delete the __expired__ EventBridge Rules it previously created. 




## Basic Configuration

| Environment Variable | Default Value | Description |
| -- | -- | -- |
| RULE_PREFIX | AUTO_ | EventBridge Rule names will be prefixed with this value. Please be careful to have this value constant from the start or _expired rule deletion_ will not function properly as it depends on the prefixes. |


## Optimizations

`aws-lambda-scheduler` optimizations can be enabled if the specified lambda invocation times **do not have to be punctual.** 



These optimizations lets you work around the maximum of 300 EventBridge Rules limitation. You can always request a quota increase for EventBridge Rules if these optimizations are not enough for your needs or if you need to be definitely punctual with your lambda calls.

Lets examine the EventBridge Rule limitations before diving into the optimization options. 

#### About EventBridge Rules
EventBridge Rules has to have:
1. schedule expression (cronjob schedule expression)
2. target (lambda function)
3. json data to call the lambda with

- EventBridge lets you create maximum of 300 Rules per region.
- EventBridge lets you define maximum of 5 targets per Rule, and targets will be invoked concurrently.

### Optimization Configuration Overview
| Environment Variable | Default Value | Optimized Values |
| -- | -- | -- |
|ALLOWED_T_MINUS_MINUTES | `None` | You specify. Setting this value to any integer will enable optimizations. |
|RULE_TARGET_ADDING_STRATEGY | `CONCURRENT_LAMBDA_TARGETS` | `INPUT_CONCATENATOR` |
|INPUT_CONCATENATOR_MODULE_NAME | `input_concatenators` | You specify.|
|INPUT_CONCATENATOR_CLASS_NAME | `None` | You have to extend a new class. |


#### config: ALLOWED_T_MINUS_MINUTES
Environment variable `ALLOWED_T_MINUS_MINUTES` defaults to nothing. If you set it as an environment variable, optimizations are enabled for `aws-lambda-scheduler`.

Let's say you have called the `aws-lambda-scheduler` and created a rule.

| Rule Name | Target | Data | Detail |
| --|-- |-- |-- |
| AUTO_2030-12-30--20-20| test-lambda | {"data":"first-rule"} | **Note:** Rule names are consisted of RULE_PREFIX and datetime its going to run.|

If you were to create another rule that would run 5 minutes after the previously created rule, without the optimizations enabled, `aws-lambda-scheduler` would create a new Rule for it.
| Rule Name | Target | Data | Detail |
| --|-- |-- |-- |
| AUTO_2030-12-30--20-20| test-lambda | {"data":"first-rule"} | |
| AUTO_2030-12-30--20-**25**| test-lambda | {"data":"second-rule"} | |

When `ALLOWED_T_MINUS_MINUTES` is set to an integer, `aws-lambda-scheduler` will look for a Rule with its `date` just before `ALLOWED_T_MINUS_MINUTES` in minutes. If there is a rule close-by, it will just add a new target to the existing rule.

Lets say `ALLOWED_T_MINUS_MINUTES` is set to `6` and we are adding rules that are 5 minutes apart.
| Rule Name | Target | Data | Detail |
| --|-- |-- |-- |
| AUTO_2030-12-30--20-20| test-lambda | {"data":"first-rule"} | first rule is created|
| AUTO_2030-12-30--20-**20**| test-lambda | {"data":"second-rule"} | second rule is appended to first rule with a new target. It would've been AUTO_2030-12-30--20-**25** if the optimizations weren't enabled.|
| AUTO_2030-12-30--20-**30**| test-lambda | {"data":"third-rule"} | There's 10 minutes of difference, so it's created as a new rule. |

Great, we've reduced our number of Rules. But this solution creates another problem: what happens if there is more than 5 targets per rule?

Simply, `aws-lambda-scheduler` will raise an exception.

>If you think you will have more than 5 targets per Rule, please continue with the other optimizations below.


**Possible solution:** We can combine the inputs of the same Lambda targets. This solution would require to implement two things:
1. a way to combine target lambdas json data (combining all the inputs of all targets)
2. Target lambda should be able to process combined data

Other optimizations can help us with these newly emerged problems. Lets continue.

#### config: RULE_TARGET_ADDING_STRATEGY

We know that we can't add more than 5 targets to a Rule.


Environment variable `RULE_TARGET_ADDING_STRATEGY` defaults to `CONCURRENT_LAMBDA_TARGETS`. With this configuration `aws-lambda-scheduler` will create more targets when we are appending an existing Rule.

Other possible value for `RULE_TARGET_ADDING_STRATEGY` is  `INPUT_CONCATENATOR`.


Setting up the `INPUT_CONCATENATOR` configuration basically lets you combine two different input json data together for the same lambda targets. And you can implement your logic of input concatenation by extending the `input_concatenators.EventBridgeInputConcatenator` abstract class. 

You only have to implement the following function and set the environment variables accordingly.
```python
def concatenate_inputs(self, existing_data, new_data):
    pass
```

There's also a ready-to-use implementation of the `EventBridgeInputConcatenator` called `EventBridgeSingleArrayInput`.

`EventBridgeSingleArrayInput` is developed to extend array inputs with the same keys of the json input. You can read more about how it works in the class comments.

Setting `INPUT_CONCATENATOR` value requires two other variables present in the environment variables: `INPUT_CONCATENATOR_MODULE_NAME` and `INPUT_CONCATENATOR_CLASS_NAME`.

| Config | Detail | Values for below example | 
|-- |-- | -- |
|INPUT_CONCATENATOR_MODULE_NAME | filename of your implementation of the abstract class.| input_concatenators  | 
| INPUT_CONCATENATOR_CLASS_NAME| name of your implementation of abstact class|  EventBridgeSingleArrayInput| 


Lets say we've added to rules for two different lambda targets for the same date.
| Rule Name | Target | Data | Detail |
| --|-- |-- |-- |
| AUTO_2030-12-30--20-20| test-lambda | {"data":"first-rule"} | first target is for  test-lambda is created|
| AUTO_2030-12-30--20-20| **other-test-lambda** | {"data":"different-rule"} | first target for **other-test-lambda**  is created|

Lets add a new target for `test-lambda` with the same `datetime_utc`. The third target looks like this before creation:

| Rule Name | Target | Data | Detail |
| --|-- |-- |-- |
| AUTO_2030-12-30--20-20| test-lambda | {"data":"third-rule"} | we have our `INPUT_CONCATENATOR` optimization enabled, and we are about to add a new target to the same rule. |

When we run `aws-lambda-scheduler` the rules get updated as this:

| Rule Name | Target | Data | Detail |
| --|-- |-- |-- |
| AUTO_2030-12-30--20-20| test-lambda | **{"data":["first-rule", "third-rule"]}** | `EventBridgeSingleArrayInput` made a list of the same keys available in the json input of the same target.|
| AUTO_2030-12-30--20-20| other-test-lambda | {"data":"different-rule"} | remains the same.|

In summary, we wanted to add 3 targets for the same rule:
- different lambda targets registered as new target for the same rule
- same lambda targets got its data updated, and **no new target or rule is created**. Input combination logic is defined by the `EventBridgeSingleArrayInput`. You can implement your own class to count for different kinds of input concatenations for your needs.







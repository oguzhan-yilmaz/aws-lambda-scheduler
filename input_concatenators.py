import abc # abstract base classes

class EventBridgeInputConcatenator(metaclass=abc.ABCMeta):

    @abc.abstractmethod
    def concatenate_inputs(self, existing_data, new_data):
        """concatenate existing data on the EventBridge Rule with the new data."""

class EventBridgeSingleArrayInput(EventBridgeInputConcatenator):
    """
    Simple example implementation of EventBridgeInputConcatenator.
    Presumes that the data passing to lambda function is a flat list.
    Expects new_data's type to be either singular entry, or another list.

    Use Case: 
        sending lambda function only an id list. let's say data sitting on the Rule target is {"object_ids":["222222"]}.
        And the new_data={"object_ids":["444444"]}. We would just extend the list with the other one and end up
        with {"object_ids":["222222", "444444"]}.
    """
    @staticmethod
    def custom_dict_value_based_update(foo: dict, bar:dict) -> dict:
        """one depth list extending dictionary updating feature. does not support nested json.
        dict.update() only looks at the keys, this functions extends the same keys
        import abc # abstract base classes
        custom_dict_value_based_update(
                foo={'a':["2","4"]},
                bar={'a':"21", "x":"x"}
            )
        returns:
            {   
                'a': ['2', '4', '21'], 
                'x': 'x'
            }        
        """
        foo_keys, bar_keys = foo.keys(), bar.keys()
        foo_clone = {k:v for k,v in foo.items()}
        unfound_keys = []
        for b_key in bar_keys:
            if b_key in foo_keys:
                b_val = bar.get(b_key)
                temp_arr = []
                if type(b_val) == list:
                    temp_arr.extend(b_val)
                elif type(b_val) in (int, str):
                    temp_arr.append(b_val)

                if temp_arr:
                    if type(foo_clone[b_key]) != list:
                        foo_clone[b_key] = [foo_clone[b_key]] # make list
                    foo_clone[b_key].extend(temp_arr)
            else:
                unfound_keys.append(b_key)

        # add the keys only on the bar dict
        for remaining_key in unfound_keys:
            foo_clone[remaining_key] = bar[remaining_key]
        return foo_clone

    def concatenate_inputs(self, existing_data: dict, new_data: dict) -> dict:
        return self.custom_dict_value_based_update(existing_data, new_data)

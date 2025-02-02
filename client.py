#! /usr/bin/env python3
'''
Клиент, посылающий случайные команды от случайных тенантов. Запускать можно сколько угодно штук в консоли с параметром <номер клиента> или <название>
$ python3 client.py A
в файлах tenant_1.txt tenant_2.txt tenant_3.txt будет лог обработки и диспетчеризации команд
'''

import json
import os
import random
import uuid
from copy import copy
from datetime import datetime
from pprint import pprint
from time import sleep

import redis
import sys


REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
r = redis.Redis(host=REDIS_HOST, port=6379, db=0)

command1 = {
    'id': None,
    'name': 'get',
    'type': 'query',
    'params': {
        'argument': 0
    }
}

command2 = {
    'id': None,
    'name': 'change',
    'type': 'command',
    'params': {
        'argument': 0
    }
}


def logger(filename, text):
    if filename:
        with open(filename, "a") as log:
            print(text, file=log)
    else:
        print(text)


class Client:
    def __init__(self, subsystem: str, client_id: str):
        self.subsystem = subsystem
        self.client_id = client_id

    def got_expected_response(self, entries, command_id):
        message_id = None
        for message_id, entry in entries:
            r.xdel(f"response-{self.client_id}", message_id)
            for event_id in entry:
                print(event_id.decode("utf-8"))
                print(command_id)
                print('-' * 10)
                print('Processing event:', event_id)
                event = json.loads(entry[event_id])
                pprint(event)
                if command_id == event_id.decode("utf-8"):
                    return event['result'], message_id
        return None, message_id

    def process_single(self, command: dict, tenant_id: str):
        # получить id последней записи из очереди events-tenant1
        # в принципе, он может как-нибудь по другому поддерживать
        # id последнего события, которое он видел
        try:
            r.xgroup_create('events', self.client_id, mkstream=True)  # будем подтверждать чтение?
        except:
            pass

        last_seen = '$'

        if command['type'] == 'query':
            counter = r.incr(f'query-{tenant_id}', 1)
            command['id'] = f'query-{tenant_id}-{counter}'
        else:
            counter = r.incr(f'command-{tenant_id}', 1)
            command['id'] = f'command-{tenant_id}-{counter}'
        command['tenant_id'] = tenant_id
        command['response-to'] = f"response-{self.client_id}"
        command['params']['argument'] = counter
        start = datetime.now()
        for f in [None, f"{tenant_id}.txt", f"client-{self.client_id}.txt"]:
            logger(f, f"client-{self.client_id} S {start} {command['id']} {command['name']}")
        message_id = r.xadd(self.subsystem, {command['id']: json.dumps(command)})

        # считывать ответы после last_id
        # когда мы видим событие, которое ждём, то можно выходить
        while True:  # TODO возможно, нужно выгребать старые сообщения тоже, плюс будет нужен таймаут
            print('wait for response')
            read = r.xread({f"response-{self.client_id}": last_seen}, count=1, block=0)  # or timeout?
            if read:
                _, entries = read[0]
                # entries = r.xrange(f"response-{client_id}", '-', '+', count=1)
                result, last_seen = self.got_expected_response(entries, command['id'])
                if result is not None:
                    end = datetime.now()
                    for f in [None, f"{tenant_id}.txt", f"client-{self.client_id}.txt"]:
                        logger(f, f"client-{self.client_id} F {end} {command['id']} {command['name']} {result}")
                    # r.xgroup_destroy('events', self.client_id)
                    return {
                        'client_id': f"client-{self.client_id}",
                        'command_id': command['id'],
                        'command_name': command['name'],
                        'result': result
                    }
                sleep(0.1)

    def routine(self):
        # получить id последней записи из очереди events-tenant1
        # в принципе, он может как-нибудь по другому поддерживать
        # id последнего события, которое он видел
        try:
            r.xgroup_create('events', self.client_id, mkstream=True)  # будем подтверждать чтение?
        except:
            pass

        last_seen = '$'

        for x in range(0, 10):
            print(f'Отправляем команду #{x}')
            command = copy(random.choice([command1, command2]))
            tenant_id = random.choice(['tenant_1', 'tenant_2', 'tenant_3'])
            if command['type'] == 'query':
                counter = r.incr(f'query-{tenant_id}', 1)
                command['id'] = f'query-{tenant_id}-{counter}'
            else:
                counter = r.incr(f'command-{tenant_id}', 1)
                command['id'] = f'command-{tenant_id}-{counter}'
            command['tenant_id'] = tenant_id
            command['response-to'] = f"response-{self.client_id}"
            command['params']['argument'] = counter
            start = datetime.now()
            for f in [None, f"{tenant_id}.txt", f"client-{self.client_id}.txt"]:
                logger(f, f"client-{self.client_id} S {start} {command['id']} {command['name']}")
            message_id = r.xadd(self.subsystem, {command['id']: json.dumps(command)})

            # считывать ответы после last_id
            # когда мы видим событие, которое ждём, то можно выходить
            while True:  # TODO возможно, нужно выгребать старые сообщения тоже, плюс будет нужен таймаут
                print(f'wait for response on {message_id}')
                read = r.xread({f"response-{self.client_id}": last_seen}, count=1, block=0)  # or timeout?
                if read:
                    _, entries = read[0]
                    # entries = r.xrange(f"response-{client_id}", '-', '+', count=1)
                    result, last_seen = self.got_expected_response(entries, command['id'])
                    if result is not None:
                        end = datetime.now()
                        for f in [None, f"{tenant_id}.txt", f"client-{self.client_id}.txt"]:
                            logger(f, f"client-{self.client_id} F {end} {command['id']} {command['name']} {result}")
                        break
                    sleep(0.1)
        r.xgroup_destroy('events', self.client_id)


if __name__ == '__main__':
    Client(subsystem=str(sys.argv[1]), client_id=str(sys.argv[2])).routine()

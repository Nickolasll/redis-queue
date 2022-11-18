#! /usr/bin/env python3
'''
Обработчик команд. Запускать можно сколько угодно штук в консоли с параметром <номер обработчика> или <название>
$ python3 worker.py 1
в файлах tenant_1.txt tenant_2.txt tenant_3.txt будет лог обработки и диспетчеризации команд
'''

import json
import os
import uuid
from datetime import datetime
from pprint import pprint
import random
from time import sleep
import sys
import redis


def fibonacci(n):
    a = 0
    b = 1
    if n < 0:
        print("Incorrect input")
    elif n == 0:
        return a
    elif n == 1:
        return b
    else:
        for i in range(2, n):
            c = a + b
            a = b
            b = c
            sleep(1)
        return b


def get(tenant_id):
    before = r.get(f'result-command-{tenant_id}')
    counter = before.decode("utf-8") if before else '0'
    fibonacci(1)  # надо чем-то занять
    after = r.get(f'result-command-{tenant_id}')
    result = after.decode("utf-8") if after else '0'
    return f'{counter} {result}'


def get_all(tenant_id):
    before = r.get(f'result-command-{tenant_id}')
    counter = before.decode("utf-8") if before else '0'
    fibonacci(4)  # надо чем-то занять
    after = r.get(f'result-command-{tenant_id}')
    result = after.decode("utf-8") if after else '0'
    return f'{counter} {result}'


REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
r = redis.Redis(host=REDIS_HOST, port=6379, db=0)
group_name = 'workers'  # общее для всех воркеров системы

COMMAND_TIMEOUT_MILLISECONDS = 5000  # таймаут команды
#COMMAND_TIMEOUT_MILLISECONDS = 30000  # таймаут команды
WORKER_TIMEOUT_MILLISECONDS = 3600000  # таймаут воркера, после которого он удаляется из consumers шины
MESSAGE_QUEUE_SIZE = 20  # как далеко мы смотрим в историю сообщений. Возможно, это зависит от числа воркеров
BLOCK_TIME_MILLISECONDS = 3000

def logger(filename, text):
    if filename:
        with open(filename, "a") as log:
            print(text, file=log)
    else:
        print(text)


class Worker:

    def __init__(self, subsystem: str, consumer_id: str):
        self.subsystem = subsystem
        self.consumer_id = consumer_id

    # todo происходит racing, когда один воркер еще не передал команду тенанта другому, а третий считает, что этот первый
    #  обрабатывает команду и клеймит на него
    def dispatched(self, message_id, tenant_id, command_id) -> bool:
        pending_entries = r.xpending_range(self.subsystem, group_name, '-', message_id, MESSAGE_QUEUE_SIZE)
        if not pending_entries:
            return False
        print(f'Всего сообщений {len(pending_entries)}')
        print(f'{self.consumer_id} Обработка команды:', message_id, command_id)
        for message in pending_entries:  # todo time_since_delivered если застряло
            request_id = message['message_id']  # bytes
            other_worker = message['consumer']  # bytes
            print(f'{self.consumer_id} проверяет сообщение {request_id} для обработчика {other_worker}')
            if self.consumer_id != other_worker.decode('utf-8'):  # в строку преобразуем
                rng = r.xrange(self.subsystem, request_id, request_id, count=1)
                if not rng:
                    print(f'непонятно, почему нет сообщения')
                    r.xack(self.subsystem, group_name, request_id)
                    continue
                _message_id, entry = rng[0]
                for _command_id in entry:
                    _command = json.loads(entry[_command_id])
                    _tenant_id = _command['tenant_id']
                    if _command_id != command_id and tenant_id == _tenant_id and \
                            _command[
                                'type'] != 'query':  # кто-то уже обрабатывает команду для этого тенанта и это не запрос
                        r.xclaim(self.subsystem, group_name, other_worker, 1, [message_id])
                        for f in [None, 'worker.txt', f"worker-{self.subsystem}-{self.consumer_id}.txt"]:
                            logger(f,
                                   f'{self.consumer_id} обработчик передает обработку {command_id} для тенанта {tenant_id} обработчику {other_worker}, потому что он уже обрабатывает команду {_command_id}')

                        return True
                    else:
                        with open(f'{_tenant_id}.txt', 'a') as log:
                            print(
                                f'{self.consumer_id} обработчик установил, что команду {_command_id} для тенанта {_tenant_id} обрабатывает {other_worker}.',
                                file=log)
        return False

    def calculate_power(self, message_id, tenant_id):
        return self.calculate_double(message_id, tenant_id)

    def calculate_double(self, message_id, tenant_id):
        try:
            _before = r.get(f'result-command-{tenant_id}')
            before = _before.decode("utf-8") if _before else '0'
            pipe = r.pipeline()
            pipe.incr(f'result-command-{tenant_id}', 1)  # увеличиваем счетчик
            fibonacci(random.randint(1, 15))  # длинная задача
            pipe.get(f'result-command-{tenant_id}')  # получаем новое значение счетчика
            if self.check_pending(message_id, self.consumer_id):  # проверяем, актуальна ли задача
                results = pipe.execute()  # коммитим изменения
                counter = str(results[0])
                result = results[1].decode("utf-8")
                return True, f'{before} {counter} {result} {result == counter}'
            else:
                # типа роллбэк
                _after = r.get(f'result-command-{tenant_id}')
                after = _after.decode("utf-8") if _after else '0'
                return False, f'отмена задачи {before} {after}'


        except KeyboardInterrupt:
            for f in [None, 'worker.txt', f"worker-{self.subsystem}-{self.consumer_id}.txt"]:
                logger(f, f'{self.consumer_id} обработчик упал')
            exit()

    def process_commands(self, entries, dispatch=True):
        for message_id, entry in entries:
            for command_id in entry:
                print('-' * 10)
                print('Обработка команды:', message_id, command_id)
                command = json.loads(entry[command_id])
                command_type = command['type']
                tenant_id = command['tenant_id']
                if not dispatch or command_type == 'query' or not self.dispatched(message_id, tenant_id, command_id):
                    start_time = datetime.now()
                    for f in [None, f"{tenant_id}.txt", "worker.txt", f"worker-{self.subsystem}-{self.consumer_id}.txt"]:
                        logger(f, f"worker-{self.subsystem}-{self.consumer_id} S {start_time} {command['id']} {command['name']}")
                    pprint(command)
                    if command['name'] == 'get':
                        argument = command['params']['argument']
                        result = get(tenant_id)
                        response = {
                            'id': command_id.decode("utf-8"),
                            'tenant_id': tenant_id,
                            'name': 'get-completed',
                            'result': result
                        }
                        self.end_command(command, command_id, message_id, response, tenant_id, result)
                    elif command['name'] == 'get_all':
                        argument = command['params']['argument']
                        result = get_all(tenant_id)
                        response = {
                            'id': command_id.decode("utf-8"),
                            'tenant_id': tenant_id,
                            'name': 'get_all-completed',
                            'result': result
                        }
                        self.end_command(command, command_id, message_id, response, tenant_id, result)
                    elif command['name'] == 'calculate-double':
                        argument = command['params']['argument']
                        finished, result = self.calculate_double(message_id, tenant_id)
                        if finished:
                            response = {
                                'id': command_id.decode("utf-8"),
                                'tenand_id': tenant_id,
                                'name': 'calculate-double-completed',
                                'result': result
                            }
                        else:
                            response = None
                        self.end_command(command, command_id, message_id, response, tenant_id, result)
                    elif command['name'] == 'calculate-power':
                        argument = command['params']['argument']
                        finished, result = self.calculate_power(message_id, tenant_id)
                        if finished:
                            response = {
                                'id': command_id.decode("utf-8"),
                                'tenand_id': tenant_id,
                                'name': 'calculate-power-completed',
                                'result': result
                            }
                        else:
                            response = None
                        self.end_command(command, command_id, message_id, response, tenant_id, result)

    def end_command(self, command, command_id, message_id, response, tenant_id, result):
        end_time = datetime.now()
        for f in [None, f"{tenant_id}.txt", "worker.txt", f"worker-{self.subsystem}-{self.consumer_id}.txt"]:
            logger(f, f"worker-{self.subsystem}-{self.consumer_id} F {end_time} {command['id']} {command['name']} {result}")
            if response:
                r.xadd(
                    command['response-to'],
                    {command_id: json.dumps(response)}
                )
        if response:
            r.xack(self.subsystem, group_name, message_id)

    def check_pending(self, message_id, consumername):
        pending_entries = r.xpending_range(self.subsystem, group_name, message_id, message_id,
                                           1)  # а вот без консумера, consumername=consumername)
        if pending_entries:
            return True
        else:
            return False

    def routine(self):
        try:
            r.xgroup_create(self.subsystem, group_name, mkstream=True)
        except:
            pass

        last_seen = '>'
        start_time = datetime.now()
        for f in [None, 'worker.txt', f"worker-{self.subsystem}-{self.consumer_id}.txt"]:
            logger(f,
                   f'{self.consumer_id} обработчик запущен {start_time} ....')

        while True:
            print('обработка зависших')
            consumers = r.xinfo_consumers(self.subsystem, group_name)
            for info in consumers:
                idle_time = info['idle']
                pending_messages = info['pending']
                consumer = info['name']
                if pending_messages == 0 and idle_time > WORKER_TIMEOUT_MILLISECONDS:
                    for f in [None, "worker.txt", f"worker-{self.subsystem}-{self.consumer_id}.txt"]:
                        logger(f, f'{self.consumer_id} обработчик удаляет отвалившийся обработчик {consumer}')
                    r.xgroup_delconsumer(self.subsystem, group_name, consumer)
                if pending_messages and idle_time > COMMAND_TIMEOUT_MILLISECONDS:
                    # забираем себе закисшие команды. TODO а что будет, если зависли команды разных тенантов?
                    entries = r.xpending_range(self.subsystem, group_name, '-', '+', MESSAGE_QUEUE_SIZE, consumername=consumer)
                    message_ids = [message['message_id'] for message in entries]
                    if message_ids:
                        r.xclaim(self.subsystem, group_name, self.consumer_id, 1, message_ids)
                        for f in [None, 'worker.txt', f"worker-{self.subsystem}-{self.consumer_id}.txt"]:
                            logger(f,
                                   f'{self.consumer_id} обработчик забирает себе обработку сообщений обработчика {consumer}, потому что он упал')

            print('обработка переданных')
            entries = r.xpending_range(self.subsystem, group_name, '-', '+', 1, consumername=self.consumer_id) # по одному, потому что может там отвис обработчик

            for message in entries:  # todo time_since_delivered
                request_id = message['message_id'].decode("utf-8")
                entries = r.xrange(self.subsystem, request_id, request_id)
                if entries:
                    self.process_commands(entries, dispatch=True)  # передиспатчиваем и тут
                else:
                    for f in [None, "worker.txt", f"worker-{self.subsystem}-{self.consumer_id}.txt"]:
                        logger(f, f'{self.consumer_id} обработчик удаляет сообщение {request_id}')
                    r.xdel(self.subsystem, request_id)
            print('новый цикл')
            # TODO last_seen должен же меняться
            entries = r.xreadgroup(group_name, self.consumer_id, {self.subsystem: last_seen}, count=1, block=BLOCK_TIME_MILLISECONDS)  # or block None??
            # полученные сообщения попадают в PEL и другие консумеры считают, что этот воркер их обрабатывает
            print(entries)
            if entries:
                _, commands = entries[0]
                self.process_commands(commands)
            sleep(0.1)  # ждем, чтобы tsd было больше 1 мс


if __name__ == '__main__':
    Worker(subsystem=str(sys.argv[1]), consumer_id=str(sys.argv[2])).routine()

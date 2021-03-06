#  Copyright (c) 2018 PaddlePaddle Authors. All Rights Reserve.
#
#Licensed under the Apache License, Version 2.0 (the "License");
#you may not use this file except in compliance with the License.
#You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#Unless required by applicable law or agreed to in writing, software
#distributed under the License is distributed on an "AS IS" BASIS,
#WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#See the License for the specific language governing permissions and
#limitations under the License.
from __future__ import print_function
import os
import numpy as np
import paddle.v2 as paddle
import paddle.v2.fluid as fluid


def convolution_net(data, label, input_dim, class_dim=2, emb_dim=32,
                    hid_dim=32):
    emb = fluid.layers.embedding(input=data, size=[input_dim, emb_dim])
    conv_3 = fluid.nets.sequence_conv_pool(
        input=emb,
        num_filters=hid_dim,
        filter_size=3,
        act="tanh",
        pool_type="sqrt")
    conv_4 = fluid.nets.sequence_conv_pool(
        input=emb,
        num_filters=hid_dim,
        filter_size=4,
        act="tanh",
        pool_type="sqrt")
    prediction = fluid.layers.fc(input=[conv_3, conv_4],
                                 size=class_dim,
                                 act="softmax")
    cost = fluid.layers.cross_entropy(input=prediction, label=label)
    avg_cost = fluid.layers.mean(x=cost)
    adam_optimizer = fluid.optimizer.Adam(learning_rate=0.002)
    optimize_ops, params_grads = adam_optimizer.minimize(avg_cost)
    accuracy = fluid.evaluator.Accuracy(input=prediction, label=label)
    return avg_cost, accuracy, accuracy.metrics[0], optimize_ops, params_grads


def to_lodtensor(data, place):
    seq_lens = [len(seq) for seq in data]
    cur_len = 0
    lod = [cur_len]
    for l in seq_lens:
        cur_len += l
        lod.append(cur_len)
    flattened_data = np.concatenate(data, axis=0).astype("int64")
    flattened_data = flattened_data.reshape([len(flattened_data), 1])
    res = fluid.LoDTensor()
    res.set(flattened_data, place)
    res.set_lod([lod])
    return res


def main():
    BATCH_SIZE = 100
    PASS_NUM = 5

    word_dict = paddle.dataset.imdb.word_dict()
    dict_dim = len(word_dict)
    class_dim = 2

    data = fluid.layers.data(
        name="words", shape=[1], dtype="int64", lod_level=1)
    label = fluid.layers.data(name="label", shape=[1], dtype="int64")
    cost, accuracy, acc_out, optimize_ops, params_grads = convolution_net(
        data, label, input_dim=dict_dim, class_dim=class_dim)

    train_data = paddle.batch(
        paddle.reader.shuffle(
            paddle.dataset.imdb.train(word_dict), buf_size=1000),
        batch_size=BATCH_SIZE)
    place = fluid.CPUPlace()
    exe = fluid.Executor(place)

    t = fluid.DistributeTranspiler()

    # all parameter server endpoints list for spliting parameters
    pserver_endpoints = os.getenv("PSERVERS")
    # server endpoint for current node
    current_endpoint = os.getenv("SERVER_ENDPOINT")
    # run as trainer or parameter server
    training_role = os.getenv(
        "TRAINING_ROLE", "TRAINER")  # get the training role: trainer/pserver
    t.transpile(
        optimize_ops, params_grads, pservers=pserver_endpoints, trainers=2)

    exe.run(fluid.default_startup_program())

    if training_role == "PSERVER":
        if not current_endpoint:
            print("need env SERVER_ENDPOINT")
            exit(1)
        pserver_prog = t.get_pserver_program(current_endpoint, optimize_ops)
        exe.run(pserver_prog)
    elif training_role == "TRAINER":
        trainer_prog = t.get_trainer_program()
        feeder = fluid.DataFeeder(feed_list=[data, label], place=place)

        for pass_id in xrange(PASS_NUM):
            accuracy.reset(exe)
            for data in train_data():
                cost_val, acc_val = exe.run(trainer_prog,
                                            feed=feeder.feed(data),
                                            fetch_list=[cost, acc_out])
                pass_acc = accuracy.eval(exe)
                print("cost=" + str(cost_val) + " acc=" + str(acc_val) +
                      " pass_acc=" + str(pass_acc))
                if cost_val < 1.0 and pass_acc > 0.8:
                    exit(0)
    else:
        print("environment var TRAINER_ROLE should be TRAINER os PSERVER")


if __name__ == '__main__':
    main()

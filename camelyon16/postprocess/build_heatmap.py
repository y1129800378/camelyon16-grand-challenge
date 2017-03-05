# Copyright 2016 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""A library to evaluate Inception on a single GPU.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import sys
sys.path.insert(0, '/home/arjun/MS/Thesis/CAMELYON-16/source')

import math
import os.path
import time
from datetime import datetime
import math
from PIL import Image
import matplotlib.pyplot as plt

from camelyon16.inception import image_processing
from camelyon16.inception import inception_model as inception
import numpy as np
import tensorflow as tf
from camelyon16.inception.dataset import Dataset
from camelyon16 import utils as utils

FLAGS = tf.app.flags.FLAGS

CKPT_PATH = utils.MODEL_CKPT_PATH

DATA_SET_NAME = 'TF-Records'
data_subset = ['train', 'validation', 'heatmap']

tf.app.flags.DEFINE_string('eval_dir', utils.EVAL_DIR,
                           """Directory where to write event logs.""")
tf.app.flags.DEFINE_string('checkpoint_dir', utils.TRAIN_DIR,
                           """Directory where to read model checkpoints.""")

# Flags governing the frequency of the eval.
tf.app.flags.DEFINE_integer('num_gpus', 2,
                            """Number of gpus.""")
tf.app.flags.DEFINE_boolean('run_once', True,
                            """Whether to run eval only once.""")

# Flags governing the data used for the eval.
tf.app.flags.DEFINE_integer('num_examples', 10000,
                            """Number of examples to run.
                            We have 10000 examples.""")
tf.app.flags.DEFINE_string('subset', 'heatmap',
                           """Either 'validation' or 'train'.""")

# tf.app.flags.DEFINE_integer('batch_size', 40,
#                             """Number of images to process in a batch.""")

BATCH_SIZE = 200


def assign_prob(heatmap_rgb, probabilities, coordinates):
    height = heatmap_rgb.shape[0] - 1
    for prob, cord in zip(probabilities[:, 1:], coordinates):
        cord = cord.decode('UTF-8')  # each cord is in form - col_row_level
        pixel_pos = cord.split('_')
        heatmap_rgb[height-int(pixel_pos[1]), int(pixel_pos[0])] = prob
    return heatmap_rgb


def generate_heatmap(saver, dataset, summary_writer, prob_ops, cords_op, summary_op, heat_map):
    # def _eval_once(saver, summary_writer, accuracy, summary_op, confusion_matrix_op, logits, labels, dense_labels):

    with tf.Session() as sess:
        ckpt = tf.train.get_checkpoint_state(FLAGS.checkpoint_dir)
        if CKPT_PATH is not None:
            saver.restore(sess, CKPT_PATH)
            global_step = CKPT_PATH.split('/')[-1].split('-')[-1]
            print('Successfully loaded model from %s at step=%s.' %
                  (CKPT_PATH, global_step))
        elif ckpt and ckpt.model_checkpoint_path:
            print(ckpt.model_checkpoint_path)
            if os.path.isabs(ckpt.model_checkpoint_path):
                # Restores from checkpoint with absolute path.
                saver.restore(sess, ckpt.model_checkpoint_path)
            else:
                # Restores from checkpoint with relative path.
                saver.restore(sess, os.path.join(FLAGS.checkpoint_dir,
                                                 ckpt.model_checkpoint_path))

            # Assuming model_checkpoint_path looks something like:
            #   /my-favorite-path/imagenet_train/model.ckpt-0,
            # extract global_step from it.
            global_step = ckpt.model_checkpoint_path.split('/')[-1].split('-')[-1]
            print('Successfully loaded model from %s at step=%s.' %
                  (ckpt.model_checkpoint_path, global_step))
        else:
            print('No checkpoint file found')
            return

        # Start the queue runners.
        coord = tf.train.Coordinator()
        try:
            threads = []
            for qr in tf.get_collection(tf.GraphKeys.QUEUE_RUNNERS):
                threads.extend(qr.create_threads(sess, coord=coord, daemon=True,
                                                 start=True))

            num_iter = int(math.ceil(dataset.num_examples_per_epoch() / BATCH_SIZE))
            step = 0
            print('%s: starting evaluation on (%s).' % (datetime.now(), FLAGS.subset))
            start_time = time.time()
            while step < num_iter and not coord.should_stop():
                probabilities, coordinates = sess.run([prob_ops, cords_op])
                heat_map = assign_prob(heat_map, probabilities, coordinates)
                step += 1
                print('%s: patch processed: %d / %d' % (datetime.now(), step * BATCH_SIZE,
                                                        dataset.num_examples_per_epoch()))
                if not ((step * BATCH_SIZE) % 1000):
                    duration = time.time() - start_time
                    print('1000 patch process time: %d secs' % math.ceil(duration))
                    start_time = time.time()

        except Exception as e:  # pylint: disable=broad-except
            coord.request_stop(e)

        coord.request_stop()
        coord.join(threads, stop_grace_period_secs=10)

    return heat_map


def build_heatmap(dataset, heat_map):
    """Evaluate model on Dataset for a number of steps."""
    with tf.Graph().as_default():
        # Get images and labels from the dataset.
        images, cords = image_processing.inputs(dataset, BATCH_SIZE)

        # Number of classes in the Dataset label set plus 1.
        # Label 0 is reserved for an (unused) background class.
        num_classes = dataset.num_classes()

        # Build a Graph that computes the logits predictions from the
        # inference model.
        _, _, prob_ops = inception.inference(images, num_classes)

        # Restore the moving average version of the learned variables for eval.
        variable_averages = tf.train.ExponentialMovingAverage(
            inception.MOVING_AVERAGE_DECAY)
        variables_to_restore = variable_averages.variables_to_restore()
        saver = tf.train.Saver(variables_to_restore)

        # Build the summary operation based on the TF collection of Summaries.
        summary_op = tf.summary.merge_all()

        graph_def = tf.get_default_graph().as_graph_def()
        summary_writer = tf.summary.FileWriter(FLAGS.eval_dir, graph_def=graph_def)

        # _eval_once(saver, summary_writer, accuracy, summary_op, confusion_matrix_op, logits, labels, dense_labels)
        heat_map = generate_heatmap(saver, dataset, summary_writer, prob_ops, cords, summary_op, heat_map)

        return heat_map


def main(unused_argv):
    tf_records_file_names = sorted(os.listdir(utils.HEAT_MAP_TF_RECORDS_DIR))
    print(tf_records_file_names)
    tf_records_file_names = tf_records_file_names[2:3]
    for wsi_filename in tf_records_file_names:
        print('Generating heatmap for: %s' % wsi_filename)
        tf_records_dir = os.path.join(utils.HEAT_MAP_TF_RECORDS_DIR, wsi_filename)
        raw_patches_dir = os.path.join(utils.HEAT_MAP_RAW_PATCHES_DIR, wsi_filename)
        heatmap_rgb_path = os.path.join(utils.HEAT_MAP_WSIs_PATH, wsi_filename)
        assert os.path.exists(heatmap_rgb_path), 'heatmap rgb image %s does not exist' % heatmap_rgb_path
        heatmap_rgb = Image.open(heatmap_rgb_path)
        heatmap_rgb = np.array(heatmap_rgb)
        heatmap_rgb = heatmap_rgb[:, :, :1]
        heatmap_rgb = np.reshape(heatmap_rgb, (heatmap_rgb.shape[0], heatmap_rgb.shape[1]))
        heat_map = np.zeros((heatmap_rgb.shape[0], heatmap_rgb.shape[1]), dtype=np.float32)
        assert os.path.exists(raw_patches_dir), 'raw patches directory %s does not exist' % raw_patches_dir
        num_patches = len(os.listdir(raw_patches_dir))
        assert os.path.exists(tf_records_dir), 'tf-records directory %s does not exist' % tf_records_dir
        dataset = Dataset(DATA_SET_NAME, data_subset[2], tf_records_dir=tf_records_dir, num_patches=num_patches)
        heat_map = build_heatmap(dataset, heat_map)
        # Image.fromarray(heat_map).save(os.path.join(utils.HEAT_MAP_DIR, wsi_filename), 'PNG')
        plt.imshow(heat_map, cmap='hot', interpolation='nearest')
        plt.colorbar()
        plt.clim(0.00, 1.00)
        plt.axis([0, heatmap_rgb.shape[1], 0, heatmap_rgb.shape[0]])
        plt.savefig(str(os.path.join(utils.HEAT_MAP_DIR, wsi_filename))+'_heatmap.png')
        plt.show()

if __name__ == '__main__':
    tf.app.run()
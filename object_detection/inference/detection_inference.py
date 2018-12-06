# Copyright 2017 The TensorFlow Authors. All Rights Reserved.
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
"""Utility functions for detection inference."""
from __future__ import division

import time
import os
import scipy.misc
import numpy as np
from object_detection.utils import visualization_utils as vis_utils
import tensorflow as tf
import util_io
import cv2
import PIL.Image as Image
from object_detection.core import standard_fields
from object_detection.core import prefetcher

DEFAULT_CATEGORY_INDEX = {1: {'id': 1, 'name': 'face'},
                          2: {'id': 2, 'name': 'eye'},
                          3: {'id': 3, 'name': 'mouth'}}


def build_input(tfrecord_paths):
  """Builds the graph's input.

  Args:
    tfrecord_paths: List of paths to the input TFRecords

  Returns:
    serialized_example_tensor: The next serialized example. String scalar Tensor
    image_tensor: The decoded image of the example. Uint8 tensor,
        shape=[1, None, None,3]
  """
  filename_queue = tf.train.string_input_producer(
      tfrecord_paths, shuffle=False, num_epochs=1)

  tf_record_reader = tf.TFRecordReader()
  _, serialized_example_tensor = tf_record_reader.read(filename_queue)

  # *** MODIFIED
  prefetch_queue = prefetcher.prefetch({'serialized_example_tensor': serialized_example_tensor}, 100)
  dequeue = prefetch_queue.dequeue()
  serialized_example_tensor = dequeue['serialized_example_tensor']

  # *** MODIFIED ENDS



  features = tf.parse_single_example(
      serialized_example_tensor,
      features={
          standard_fields.TfExampleFields.image_encoded:
              tf.FixedLenFeature([], tf.string),
      })
  encoded_image = features[standard_fields.TfExampleFields.image_encoded]
  image_tensor = tf.image.decode_image(encoded_image, channels=3)
  image_tensor.set_shape([None, None, 3])
  image_tensor = tf.expand_dims(image_tensor, 0)

  # # *** MODIFIED
  # batch = tf.train.batch(
  #   [serialized_example_tensor, image_tensor],
  #   batch_size=24,
  #   enqueue_many=False,
  #   num_threads=6,
  #   capacity=5 * 24)
  # return batch[0], batch[1]
  # # *** MODIFIED ENDS


  return serialized_example_tensor, image_tensor


def build_inference_graph(image_tensor, inference_graph_path, override_num_detections=None):
  """Loads the inference graph and connects it to the input image.

  Args:
    image_tensor: The input image. uint8 tensor, shape=[1, None, None, 3]
    inference_graph_path: Path to the inference graph with embedded weights

  Returns:
    detected_boxes_tensor: Detected boxes. Float tensor,
        shape=[num_detections, 4]
    detected_scores_tensor: Detected scores. Float tensor,
        shape=[num_detections]
    detected_labels_tensor: Detected labels. Int64 tensor,
        shape=[num_detections]
  """
  with tf.gfile.Open(inference_graph_path, 'rb') as graph_def_file:
    graph_content = graph_def_file.read()
  graph_def = tf.GraphDef()
  graph_def.MergeFromString(graph_content)

  tf.import_graph_def(
      graph_def, name='', input_map={'image_tensor': image_tensor})

  g = tf.get_default_graph()

  if override_num_detections is not None:
    num_detections_tensor = tf.cast(override_num_detections, tf.int32)
  else:
    num_detections_tensor = tf.squeeze(
        g.get_tensor_by_name('num_detections:0'), 0)
    num_detections_tensor = tf.cast(num_detections_tensor, tf.int32)

  detected_boxes_tensor = tf.squeeze(
      g.get_tensor_by_name('detection_boxes:0'), 0)
  detected_boxes_tensor = detected_boxes_tensor[:num_detections_tensor]

  detected_scores_tensor = tf.squeeze(
      g.get_tensor_by_name('detection_scores:0'), 0)
  detected_scores_tensor = detected_scores_tensor[:num_detections_tensor]

  detected_labels_tensor = tf.squeeze(
      g.get_tensor_by_name('detection_classes:0'), 0)
  detected_labels_tensor = tf.cast(detected_labels_tensor, tf.int64)
  detected_labels_tensor = detected_labels_tensor[:num_detections_tensor]

  return detected_boxes_tensor, detected_scores_tensor, detected_labels_tensor


def infer_detections_and_add_to_example(
    sess,  # Modified.
    serialized_example_tensor, image_tensor, detected_boxes_tensor, detected_scores_tensor,
    detected_labels_tensor, discard_image_pixels):
  """Runs the supplied tensors and adds the inferred detections to the example.

  Args:
    serialized_example_tensor: Serialized TF example. Scalar string tensor
    detected_boxes_tensor: Detected boxes. Float tensor,
        shape=[num_detections, 4]
    detected_scores_tensor: Detected scores. Float tensor,
        shape=[num_detections]
    detected_labels_tensor: Detected labels. Int64 tensor,
        shape=[num_detections]
    discard_image_pixels: If true, discards the image from the result
  Returns:
    The de-serialized TF example augmented with the inferred detections.
  """
  tf_example = tf.train.Example()
  (serialized_example, image, detected_boxes, detected_scores,
   detected_classes) = sess.run([  # Modified from tf.get_default_session() to sess
       serialized_example_tensor, image_tensor, detected_boxes_tensor, detected_scores_tensor,
       detected_labels_tensor
   ])
  detected_boxes = detected_boxes.T

  tf_example.ParseFromString(serialized_example)
  feature = tf_example.features.feature
  feature[standard_fields.TfExampleFields.
          detection_score].float_list.value[:] = detected_scores
  feature[standard_fields.TfExampleFields.
          detection_bbox_ymin].float_list.value[:] = detected_boxes[0]
  feature[standard_fields.TfExampleFields.
          detection_bbox_xmin].float_list.value[:] = detected_boxes[1]
  feature[standard_fields.TfExampleFields.
          detection_bbox_ymax].float_list.value[:] = detected_boxes[2]
  feature[standard_fields.TfExampleFields.
          detection_bbox_xmax].float_list.value[:] = detected_boxes[3]
  feature[standard_fields.TfExampleFields.
          detection_class_label].int64_list.value[:] = detected_classes

  do_save_image = True
  if do_save_image:
    # TODO: hard coded for our purpose for now.
    category_index = {1: {'id': 1, 'name': 'anime_figure'}}
    annotated_image = vis_utils.visualize_boxes_and_labels_on_image_array(
        image[0],
      detected_boxes.T,
      detected_classes,
      detected_scores,
        category_index,
        use_normalized_coordinates=True,
      min_score_thresh=.5,
      max_boxes_to_draw=20,
        agnostic_mode=False,
        skip_scores=True,
        skip_labels=True)
    # For debugging.
    scipy.misc.imsave(
      os.path.join('/media/jerryli27/Data_Disk_HDD_3/DanbooruData/DanbooruData/anime_human_segmentation_rcnn_inception_v3_inference/danbooru1m_segmented/sample_images', 'mask_sample_%d.png' %int(time.time())),
      annotated_image)
  #

  if discard_image_pixels:
    del feature[standard_fields.TfExampleFields.image_encoded]

  return tf_example


  # # *** MODIFIED
  # This does not work because each image inthe batch has a different size!
  # (serialized_example_batched, detected_boxes_batched, detected_scores_batched,
  #  detected_classes_batched) = sess.run([
  #      serialized_example_tensor, detected_boxes_tensor, detected_scores_tensor,
  #      detected_labels_tensor
  #  ])
  # ret = []
  # for (serialized_example, detected_boxes, detected_scores,
  #  detected_classes) in zip(serialized_example_batched, detected_boxes_batched, detected_scores_batched,
  #  detected_classes_batched):
  #   tf_example = tf.train.Example()
  #   detected_boxes = detected_boxes.T
  #
  #   tf_example.ParseFromString(serialized_example)
  #   feature = tf_example.features.feature
  #   feature[standard_fields.TfExampleFields.
  #           detection_score].float_list.value[:] = detected_scores
  #   feature[standard_fields.TfExampleFields.
  #           detection_bbox_ymin].float_list.value[:] = detected_boxes[0]
  #   feature[standard_fields.TfExampleFields.
  #           detection_bbox_xmin].float_list.value[:] = detected_boxes[1]
  #   feature[standard_fields.TfExampleFields.
  #           detection_bbox_ymax].float_list.value[:] = detected_boxes[2]
  #   feature[standard_fields.TfExampleFields.
  #           detection_bbox_xmax].float_list.value[:] = detected_boxes[3]
  #   feature[standard_fields.TfExampleFields.
  #           detection_class_label].int64_list.value[:] = detected_classes
  #
  #   if discard_image_pixels:
  #     del feature[standard_fields.TfExampleFields.image_encoded]
  #   ret.append(tf_example)
  #
  # return ret



def infer_detections(
    sess, image_tensor, detected_tensors, min_score_thresh=.5, visualize_inference=False, category_index=None,  feed_dict=None):
  """Runs the supplied tensors and adds the inferred detections to the example.

  Args:
    visualize_inference: If true, return image annotated with detection results.
  Returns:
    A dictionary with detection results.
  """
  # detected_boxes_tensor: Detected boxes. Float tensor,
  #     shape=[num_detections, 4]
  # detected_scores_tensor: Detected scores. Float tensor,
  #     shape=[num_detections]
  # detected_labels_tensor: Detected labels. Int64 tensor,
  #     shape=[num_detections]
  (detected_boxes_tensor, detected_scores_tensor, detected_labels_tensor) = detected_tensors

  (image, detected_boxes, detected_scores,
   detected_classes) = sess.run([  # Modified from tf.get_default_session() to sess
       image_tensor, detected_boxes_tensor, detected_scores_tensor,
       detected_labels_tensor
   ], feed_dict=feed_dict)
  detected_boxes = detected_boxes.T

  indices = detected_scores > min_score_thresh
  ret = {
    'detection_score': detected_scores[indices].tolist(),
    'detection_bbox_ymin': detected_boxes[0][indices].tolist(),
    'detection_bbox_xmin': detected_boxes[1][indices].tolist(),
    'detection_bbox_ymax': detected_boxes[2][indices].tolist(),
    'detection_bbox_xmax': detected_boxes[3][indices].tolist(),
    'detection_class_label': detected_classes[indices].tolist(),
  }
  if visualize_inference:
    annotated_image = vis_utils.visualize_boxes_and_labels_on_image_array(
      image[0],
      detected_boxes.T,
      detected_classes,
      detected_scores,
      category_index or DEFAULT_CATEGORY_INDEX,
      use_normalized_coordinates=True,
      min_score_thresh=min_score_thresh,
      max_boxes_to_draw=20,
      agnostic_mode=False,
      skip_scores=False,
      skip_labels=False)
    ret['annotated_image'] = annotated_image
  return ret

def crop_out(
    sess, image_tensor, detected_tensors, min_score_thresh=.5, visualize_inference=False, category_index=None,  feed_dict=None, img_np = None, filename=None):

  (detected_boxes_tensor, detected_scores_tensor, detected_labels_tensor) = detected_tensors

  (image, detected_boxes, detected_scores,
   detected_classes) = sess.run([  # Modified from tf.get_default_session() to sess
       image_tensor, detected_boxes_tensor, detected_scores_tensor,
       detected_labels_tensor
   ], feed_dict=feed_dict)
  detected_boxes = detected_boxes.T

  indices = detected_scores > min_score_thresh
  # print(type(detected_boxes[0][indices]))

  ymins = detected_boxes[0][indices]
  xmins = detected_boxes[1][indices]
  ymaxs = detected_boxes[2][indices]
  xmaxs = detected_boxes[3][indices]
  class_label = detected_classes[indices].tolist()
  image = image[0]
  v_center = (ymaxs+ymins)/2
  h_center = (xmaxs+xmins)/2
  half_height = (ymaxs-ymins)/2
  half_width = (xmaxs-xmins)/2
  v_scale = 1.6
  h_scale = 2

  ymins = v_center - v_scale*half_height
  ymaxs = v_center + v_scale*half_height
  xmins = h_center - h_scale*half_width
  xmaxs = h_center + h_scale*half_width
  # print(image.shape)
  # print(img_np.shape)
  # print(class_label)


  for i,c in enumerate(class_label):
    eye_count=0
    if c==2:
        # print(image.shape)
        # image = np.clip(image, 0, 255).astype(np.uint8)
        eye_img = image[int(ymins[i]*image.shape[0]):int(ymaxs[i]*image.shape[0]), int(xmins[i]*image.shape[1]):int(xmaxs[i]*image.shape[1])]
        util_io.imsave(filename[:-4]+'_'+str(eye_count)+'.png', eye_img)
        # eye_img = np.array(Image.fromarray(np.uint8(eye_img)).convert('RGB'))
        # cv2.imwrite(filename[:-4]+'_'+str(eye_count)+'.png',eye_img)
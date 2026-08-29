#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause-Clear

gst-launch-1.0 -e --gst-debug=2 \
qtivcomposer name=comp sink_0::position="<0, 0>" sink_0::dimensions="<240, 135>" sink_1::position="<240, 0>" sink_1::dimensions="<240, 135>" sink_2::position="<480, 0>" sink_2::dimensions="<240, 135>" sink_3::position="<720, 0>" sink_3::dimensions="<240, 135>" sink_4::position="<960, 0>" sink_4::dimensions="<240, 135>" sink_5::position="<1200, 0>" sink_5::dimensions="<240, 135>" sink_6::position="<1440, 0>" sink_6::dimensions="<240, 135>" sink_7::position="<1680, 0>" sink_7::dimensions="<240, 135>" sink_8::position="<0, 135>" sink_8::dimensions="<240, 135>" sink_9::position="<240, 135>" sink_9::dimensions="<240, 135>" sink_10::position="<480, 135>" sink_10::dimensions="<240, 135>" sink_11::position="<720, 135>" sink_11::dimensions="<240, 135>" sink_12::position="<960, 135>" sink_12::dimensions="<240, 135>" sink_13::position="<1200, 135>" sink_13::dimensions="<240, 135>" sink_14::position="<1440, 135>" sink_14::dimensions="<240, 135>" sink_15::position="<1680, 135>" sink_15::dimensions="<240, 135>" sink_16::position="<0, 270>" sink_16::dimensions="<240, 135>" sink_17::position="<240, 270>" sink_17::dimensions="<240, 135>" sink_18::position="<480, 270>" sink_18::dimensions="<240, 135>" sink_19::position="<720, 270>" sink_19::dimensions="<240, 135>" sink_20::position="<960, 270>" sink_20::dimensions="<240, 135>" sink_21::position="<1200, 270>" sink_21::dimensions="<240, 135>" sink_22::position="<1440, 270>" sink_22::dimensions="<240, 135>" sink_23::position="<1680, 270>" sink_23::dimensions="<240, 135>" sink_24::position="<0, 405>" sink_24::dimensions="<240, 135>" sink_25::position="<240, 405>" sink_25::dimensions="<240, 135>" sink_26::position="<480, 405>" sink_26::dimensions="<240, 135>" sink_27::position="<720, 405>" sink_27::dimensions="<240, 135>" sink_28::position="<960, 405>" sink_28::dimensions="<240, 135>" sink_29::position="<1200, 405>" sink_29::dimensions="<240, 135>" sink_30::position="<1440, 405>" sink_30::dimensions="<240, 135>" sink_31::position="<1680, 405>" sink_31::dimensions="<240, 135>" ! \
  queue ! waylandsink fullscreen=true sync=true \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t0 ! qtimetamux name=mux0 ! queue ! qtivoverlay ! queue ! comp.sink_0 \
  t0. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux0. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t1 ! qtimetamux name=mux1 ! queue ! qtivoverlay ! queue ! comp.sink_1 \
  t1. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux1. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t2 ! qtimetamux name=mux2 ! queue ! qtivoverlay ! queue ! comp.sink_2 \
  t2. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux2. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t3 ! qtimetamux name=mux3 ! queue ! qtivoverlay ! queue ! comp.sink_3 \
  t3. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux3. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t4 ! qtimetamux name=mux4 ! queue ! qtivoverlay ! queue ! comp.sink_4 \
  t4. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux4. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t5 ! qtimetamux name=mux5 ! queue ! qtivoverlay ! queue ! comp.sink_5 \
  t5. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux5. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t6 ! qtimetamux name=mux6 ! queue ! qtivoverlay ! queue ! comp.sink_6 \
  t6. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux6. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t7 ! qtimetamux name=mux7 ! queue ! qtivoverlay ! queue ! comp.sink_7 \
  t7. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux7. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t8 ! qtimetamux name=mux8 ! queue ! qtivoverlay ! queue ! comp.sink_8 \
  t8. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux8. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t9 ! qtimetamux name=mux9 ! queue ! qtivoverlay ! queue ! comp.sink_9 \
  t9. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux9. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t10 ! qtimetamux name=mux10 ! queue ! qtivoverlay ! queue ! comp.sink_10 \
  t10. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux10. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t11 ! qtimetamux name=mux11 ! queue ! qtivoverlay ! queue ! comp.sink_11 \
  t11. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux11. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t12 ! qtimetamux name=mux12 ! queue ! qtivoverlay ! queue ! comp.sink_12 \
  t12. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux12. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t13 ! qtimetamux name=mux13 ! queue ! qtivoverlay ! queue ! comp.sink_13 \
  t13. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux13. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t14 ! qtimetamux name=mux14 ! queue ! qtivoverlay ! queue ! comp.sink_14 \
  t14. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux14. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t15 ! qtimetamux name=mux15 ! queue ! qtivoverlay ! queue ! comp.sink_15 \
  t15. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux15. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t16 ! qtimetamux name=mux16 ! queue ! qtivoverlay ! queue ! comp.sink_16 \
  t16. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux16. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t17 ! qtimetamux name=mux17 ! queue ! qtivoverlay ! queue ! comp.sink_17 \
  t17. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux17. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t18 ! qtimetamux name=mux18 ! queue ! qtivoverlay ! queue ! comp.sink_18 \
  t18. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux18. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t19 ! qtimetamux name=mux19 ! queue ! qtivoverlay ! queue ! comp.sink_19 \
  t19. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux19. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t20 ! qtimetamux name=mux20 ! queue ! qtivoverlay ! queue ! comp.sink_20 \
  t20. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux20. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t21 ! qtimetamux name=mux21 ! queue ! qtivoverlay ! queue ! comp.sink_21 \
  t21. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux21. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t22 ! qtimetamux name=mux22 ! queue ! qtivoverlay ! queue ! comp.sink_22 \
  t22. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux22. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t23 ! qtimetamux name=mux23 ! queue ! qtivoverlay ! queue ! comp.sink_23 \
  t23. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux23. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t24 ! qtimetamux name=mux24 ! queue ! qtivoverlay ! queue ! comp.sink_24 \
  t24. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux24. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t25 ! qtimetamux name=mux25 ! queue ! qtivoverlay ! queue ! comp.sink_25 \
  t25. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux25. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t26 ! qtimetamux name=mux26 ! queue ! qtivoverlay ! queue ! comp.sink_26 \
  t26. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux26. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t27 ! qtimetamux name=mux27 ! queue ! qtivoverlay ! queue ! comp.sink_27 \
  t27. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux27. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t28 ! qtimetamux name=mux28 ! queue ! qtivoverlay ! queue ! comp.sink_28 \
  t28. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux28. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t29 ! qtimetamux name=mux29 ! queue ! qtivoverlay ! queue ! comp.sink_29 \
  t29. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux29. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t30 ! qtimetamux name=mux30 ! queue ! qtivoverlay ! queue ! comp.sink_30 \
  t30. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux30. \
  filesrc location=/etc/mahendra/gesture_sample.mp4 ! qtdemux ! h265parse ! \
  v4l2h265dec capture-io-mode=4 output-io-mode=4 ! video/x-raw,format=NV12 ! queue ! \
  tee name=t31 ! qtimetamux name=mux31 ! queue ! qtivoverlay ! queue ! comp.sink_31 \
  t31. ! queue ! qtimlvconverter ! queue ! \
  qtimlqnn model=/etc/mahendra/inception_v3_w8a8.bin backend=/usr/lib/libQnnHtp.so system=/usr/lib/libQnnSystem.so ! queue ! \
  qtimlpostprocess module=mobilenet labels=/etc/mahendra/classification.json settings="{\"confidence\": 51.0}" ! \
  text/x-raw ! queue ! mux31.

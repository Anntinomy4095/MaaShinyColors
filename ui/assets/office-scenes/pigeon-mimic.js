export default {
  "version": 1,
  "name": "跟着歪头",
  "segments": [
    {
      "motion": {
        "version": 1,
        "character": "mano",
        "target": "character",
        "baseAnimation": "wait",
        "startTime": 0,
        "duration": 4,
        "fps": 30,
        "loop": true,
        "tracks": {},
        "name": "靠近",
        "sourceEnd": 4,
        "sourceLoop": false
      },
      "label": "靠近"
    },
    {
      "motion": {
        "version": 1,
        "character": "mano",
        "target": "character",
        "baseAnimation": "wait",
        "startTime": 4,
        "duration": 3,
        "fps": 30,
        "loop": true,
        "tracks": {
          "head": [
            {
              "time": 0,
              "x": 0,
              "y": 0,
              "rotation": 0,
              "curve": "smooth"
            },
            {
              "time": 0.3,
              "x": 0,
              "y": 0,
              "rotation": 5,
              "curve": "smooth"
            },
            {
              "time": 0.7666666666666667,
              "x": 0,
              "y": 0,
              "rotation": 5,
              "curve": "smooth"
            },
            {
              "time": 1.1666666666666667,
              "x": 0,
              "y": 0,
              "rotation": -4,
              "curve": "smooth"
            },
            {
              "time": 1.6,
              "x": 0,
              "y": 0,
              "rotation": -4,
              "curve": "smooth"
            },
            {
              "time": 2.1666666666666665,
              "x": 0,
              "y": 0,
              "rotation": 2,
              "curve": "smooth"
            },
            {
              "time": 3,
              "x": 0,
              "y": 0,
              "rotation": 0,
              "curve": "smooth"
            }
          ],
          "Pchan": [
            {
              "time": 0,
              "x": 0,
              "y": 0,
              "rotation": 0,
              "curve": "smooth"
            },
            {
              "time": 0.6,
              "x": 0,
              "y": 0,
              "rotation": 12,
              "curve": "smooth"
            },
            {
              "time": 1,
              "x": 0,
              "y": 0,
              "rotation": 12,
              "curve": "smooth"
            },
            {
              "time": 1.5,
              "x": 0,
              "y": 0,
              "rotation": -10,
              "curve": "smooth"
            },
            {
              "time": 1.9,
              "x": 0,
              "y": 0,
              "rotation": -10,
              "curve": "smooth"
            },
            {
              "time": 2.4,
              "x": 0,
              "y": 0,
              "rotation": 4,
              "curve": "smooth"
            },
            {
              "time": 3,
              "x": 0,
              "y": 0,
              "rotation": 0,
              "curve": "smooth"
            }
          ],
          "body": [
            {
              "time": 0,
              "x": 0,
              "y": 0,
              "rotation": 0,
              "curve": "smooth"
            },
            {
              "time": 0.6,
              "x": 0,
              "y": 0,
              "rotation": 1.5,
              "curve": "smooth"
            },
            {
              "time": 1.5,
              "x": 0,
              "y": 0,
              "rotation": -1.5,
              "curve": "smooth"
            },
            {
              "time": 2.4,
              "x": 0,
              "y": 0,
              "rotation": 0.5,
              "curve": "smooth"
            },
            {
              "time": 3,
              "x": 0,
              "y": 0,
              "rotation": 0,
              "curve": "smooth"
            }
          ]
        },
        "name": "真乃与鸽子 · 歪头",
        "sourceEnd": 5.3,
        "sourceLoop": false
      },
      "label": "模仿"
    },
    {
      "motion": {
        "version": 1,
        "character": "mano",
        "target": "character",
        "baseAnimation": "wait",
        "startTime": 5.3,
        "duration": 5.2,
        "fps": 30,
        "loop": true,
        "tracks": {},
        "name": "返回",
        "sourceEnd": 10.5,
        "sourceLoop": false
      },
      "label": "返回"
    }
  ]
};

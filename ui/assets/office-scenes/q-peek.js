export default {
  "version": 2,
  "name": "团扇后面",
  "duration": 12.5,
  "actors": [
    {
      "id": "mano",
      "x": 0.36,
      "view": "full",
      "flip": true,
      "segments": [
        {
          "at": 0,
          "motion": {
            "version": 1,
            "character": "mano",
            "target": "character",
            "baseAnimation": "wait",
            "startTime": 0,
            "duration": 10.5,
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
                  "time": 5.4,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 6.1,
                  "x": 0,
                  "y": 0,
                  "rotation": 4.5,
                  "curve": "smooth"
                },
                {
                  "time": 6.6,
                  "x": 0,
                  "y": 0,
                  "rotation": 4.5,
                  "curve": "smooth"
                },
                {
                  "time": 7.3,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 8,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 8.6,
                  "x": 0,
                  "y": 0,
                  "rotation": -4,
                  "curve": "smooth"
                },
                {
                  "time": 8.9,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 9.2,
                  "x": 0,
                  "y": 0,
                  "rotation": 3,
                  "curve": "smooth"
                },
                {
                  "time": 9.5,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 10.5,
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
                  "time": 7.8,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 8.3,
                  "x": 0,
                  "y": 0,
                  "rotation": 9,
                  "curve": "smooth"
                },
                {
                  "time": 8.666666666666666,
                  "x": 0,
                  "y": 0,
                  "rotation": 9,
                  "curve": "smooth"
                },
                {
                  "time": 9.1,
                  "x": 0,
                  "y": 0,
                  "rotation": -7,
                  "curve": "smooth"
                },
                {
                  "time": 9.4,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 10.5,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                }
              ]
            },
            "modelKind": "cb",
            "name": "mano-bird",
            "sourceEnd": 10.5,
            "worldPositionBones": [
              "Pchan"
            ]
          }
        },
        {
          "at": 10.5,
          "motion": {
            "version": 1,
            "character": "mano",
            "target": "character",
            "baseAnimation": "wait",
            "startTime": 0,
            "duration": 2,
            "fps": 30,
            "loop": true,
            "tracks": {},
            "modelKind": "cb",
            "name": "mano-rest",
            "sourceEnd": 2
          },
          "mix": 0.2
        }
      ]
    },
    {
      "id": "tenka",
      "x": 0.64,
      "view": "full",
      "segments": [
        {
          "at": 0,
          "motion": {
            "version": 1,
            "character": "tenka",
            "target": "character",
            "baseAnimation": "wait",
            "startTime": 0,
            "duration": 3.2,
            "fps": 30,
            "loop": true,
            "tracks": {},
            "modelKind": "cb",
            "name": "tenka-notice",
            "sourceEnd": 3.2
          }
        },
        {
          "at": 3.2,
          "motion": {
            "version": 1,
            "character": "tenka",
            "target": "character",
            "baseAnimation": "wait",
            "startTime": 11.633333,
            "duration": 0.7,
            "fps": 30,
            "loop": true,
            "tracks": {},
            "modelKind": "cb",
            "name": "tenka-reset",
            "sourceEnd": 12.333333
          },
          "mix": 0.25
        },
        {
          "at": 3.9,
          "motion": {
            "version": 1,
            "character": "tenka",
            "target": "character",
            "baseAnimation": "wait",
            "startTime": 0,
            "duration": 2,
            "fps": 30,
            "loop": true,
            "tracks": {},
            "modelKind": "cb",
            "name": "tenka-fan",
            "sourceEnd": 2
          },
          "mix": 0.15
        },
        {
          "at": 5.9,
          "motion": {
            "version": 1,
            "character": "tenka",
            "target": "character",
            "baseAnimation": "wait",
            "startTime": 2,
            "duration": 3.2,
            "fps": 30,
            "loop": true,
            "tracks": {
              "body2": [
                {
                  "time": 0,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 0.4,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 0.9,
                  "x": 0,
                  "y": 0,
                  "rotation": 2.6,
                  "curve": "smooth"
                },
                {
                  "time": 1.1,
                  "x": 0,
                  "y": 0,
                  "rotation": 2.6,
                  "curve": "smooth"
                },
                {
                  "time": 1.7,
                  "x": 0,
                  "y": 0,
                  "rotation": 4,
                  "curve": "smooth"
                },
                {
                  "time": 1.9666666666666666,
                  "x": 0,
                  "y": 0,
                  "rotation": 4,
                  "curve": "smooth"
                },
                {
                  "time": 2.3333333333333335,
                  "x": 0,
                  "y": 0,
                  "rotation": 2,
                  "curve": "smooth"
                },
                {
                  "time": 2.8,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 3.2,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                }
              ],
              "head": [
                {
                  "time": 0,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 0.4,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 0.9,
                  "x": 0,
                  "y": 0,
                  "rotation": -7.15,
                  "curve": "smooth"
                },
                {
                  "time": 1.1,
                  "x": 0,
                  "y": 0,
                  "rotation": -7.15,
                  "curve": "smooth"
                },
                {
                  "time": 1.7,
                  "x": 0,
                  "y": 0,
                  "rotation": -11,
                  "curve": "smooth"
                },
                {
                  "time": 1.9666666666666666,
                  "x": 0,
                  "y": 0,
                  "rotation": -11,
                  "curve": "smooth"
                },
                {
                  "time": 2.3333333333333335,
                  "x": 0,
                  "y": 0,
                  "rotation": -5.5,
                  "curve": "smooth"
                },
                {
                  "time": 2.8,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 3.2,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                }
              ],
              "arm_R_top": [
                {
                  "time": 0,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 0.4,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 0.9,
                  "x": 0,
                  "y": 0,
                  "rotation": -3.9000000000000004,
                  "curve": "smooth"
                },
                {
                  "time": 1.1,
                  "x": 0,
                  "y": 0,
                  "rotation": -3.9000000000000004,
                  "curve": "smooth"
                },
                {
                  "time": 1.7,
                  "x": 0,
                  "y": 0,
                  "rotation": -6,
                  "curve": "smooth"
                },
                {
                  "time": 1.9666666666666666,
                  "x": 0,
                  "y": 0,
                  "rotation": -6,
                  "curve": "smooth"
                },
                {
                  "time": 2.3333333333333335,
                  "x": 0,
                  "y": 0,
                  "rotation": -3,
                  "curve": "smooth"
                },
                {
                  "time": 2.8,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 3.2,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                }
              ],
              "arm_R_bottom": [
                {
                  "time": 0,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 0.4,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 0.9,
                  "x": 0,
                  "y": 0,
                  "rotation": 7.15,
                  "curve": "smooth"
                },
                {
                  "time": 1.1,
                  "x": 0,
                  "y": 0,
                  "rotation": 7.15,
                  "curve": "smooth"
                },
                {
                  "time": 1.7,
                  "x": 0,
                  "y": 0,
                  "rotation": 11,
                  "curve": "smooth"
                },
                {
                  "time": 1.9666666666666666,
                  "x": 0,
                  "y": 0,
                  "rotation": 11,
                  "curve": "smooth"
                },
                {
                  "time": 2.3333333333333335,
                  "x": 0,
                  "y": 0,
                  "rotation": 5.5,
                  "curve": "smooth"
                },
                {
                  "time": 2.8,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 3.2,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                }
              ],
              "hand_R": [
                {
                  "time": 0,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 0.4,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 0.9,
                  "x": 0,
                  "y": 0,
                  "rotation": 4.55,
                  "curve": "smooth"
                },
                {
                  "time": 1.1,
                  "x": 0,
                  "y": 0,
                  "rotation": 4.55,
                  "curve": "smooth"
                },
                {
                  "time": 1.7,
                  "x": 0,
                  "y": 0,
                  "rotation": 7,
                  "curve": "smooth"
                },
                {
                  "time": 1.9666666666666666,
                  "x": 0,
                  "y": 0,
                  "rotation": 7,
                  "curve": "smooth"
                },
                {
                  "time": 2.3333333333333335,
                  "x": 0,
                  "y": 0,
                  "rotation": 3.5,
                  "curve": "smooth"
                },
                {
                  "time": 2.8,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                },
                {
                  "time": 3.2,
                  "x": 0,
                  "y": 0,
                  "rotation": 0,
                  "curve": "smooth"
                }
              ]
            },
            "modelKind": "cb",
            "name": "tenka-peek",
            "sourceEnd": 3.2
          },
          "mix": 0.12
        },
        {
          "at": 9.1,
          "motion": {
            "version": 1,
            "character": "tenka",
            "target": "character",
            "baseAnimation": "wait",
            "startTime": 11.633333,
            "duration": 0.7,
            "fps": 30,
            "loop": true,
            "tracks": {},
            "modelKind": "cb",
            "name": "tenka-reset",
            "sourceEnd": 12.333333
          },
          "mix": 0.25
        },
        {
          "at": 9.8,
          "motion": {
            "version": 1,
            "character": "tenka",
            "target": "character",
            "baseAnimation": "wait",
            "startTime": 0,
            "duration": 2,
            "fps": 30,
            "loop": true,
            "tracks": {},
            "modelKind": "cb",
            "name": "tenka-fan",
            "sourceEnd": 2
          },
          "mix": 0.15
        },
        {
          "at": 11.8,
          "motion": {
            "version": 1,
            "character": "tenka",
            "target": "character",
            "baseAnimation": "wait",
            "startTime": 0,
            "duration": 0.7,
            "fps": 30,
            "loop": true,
            "tracks": {},
            "modelKind": "cb",
            "name": "tenka-tail",
            "sourceEnd": 0.7
          },
          "mix": 0.15
        }
      ]
    }
  ]
};

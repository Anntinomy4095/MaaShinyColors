export default {
  "version": 2,
  "name": "迟半拍的招呼",
  "duration": 9.2,
  "actors": [
    {
      "id": "meguru",
      "x": 0.31,
      "view": "upper",
      "segments": [
        {
          "at": 0,
          "motion": {
            "version": 1,
            "character": "meguru",
            "target": "character",
            "baseAnimation": "hello",
            "startTime": 0,
            "duration": 4.666667,
            "fps": 30,
            "loop": true,
            "tracks": {},
            "modelKind": "stand",
            "name": "meguru-hello",
            "sourceEnd": 4.666667,
            "sourceLoop": false,
            "gazeAnimation": "eye_right"
          }
        },
        {
          "at": 4.666667,
          "motion": {
            "version": 1,
            "character": "meguru",
            "target": "character",
            "baseAnimation": "smile1",
            "startTime": 0.6,
            "duration": 4.533333,
            "fps": 30,
            "loop": true,
            "tracks": {},
            "modelKind": "stand",
            "name": "meguru-smile",
            "sourceEnd": 5.133333,
            "sourceLoop": false,
            "gazeAnimation": "eye_right"
          },
          "mix": 0.3
        }
      ]
    },
    {
      "id": "mano",
      "x": 0.69,
      "view": "upper",
      "segments": [
        {
          "at": 0,
          "motion": {
            "version": 1,
            "character": "mano",
            "target": "character",
            "baseAnimation": "wait",
            "startTime": 0,
            "duration": 1.1,
            "fps": 30,
            "loop": true,
            "tracks": {},
            "modelKind": "stand",
            "name": "mano-notice",
            "sourceEnd": 1.1,
            "sourceLoop": false,
            "gazeAnimation": "eye_left"
          }
        },
        {
          "at": 1.1,
          "motion": {
            "version": 1,
            "character": "mano",
            "target": "character",
            "baseAnimation": "hello",
            "startTime": 0,
            "duration": 4.233333,
            "fps": 30,
            "loop": true,
            "tracks": {},
            "modelKind": "stand",
            "name": "mano-hello",
            "sourceEnd": 4.233333,
            "sourceLoop": false,
            "gazeAnimation": "eye_left"
          },
          "mix": 0.35
        },
        {
          "at": 5.333333,
          "motion": {
            "version": 1,
            "character": "mano",
            "target": "character",
            "baseAnimation": "smile2",
            "startTime": 0,
            "duration": 3.866667,
            "fps": 30,
            "loop": true,
            "tracks": {},
            "modelKind": "stand",
            "name": "mano-smile",
            "sourceEnd": 3.866667,
            "sourceLoop": false,
            "gazeAnimation": "eye_left"
          },
          "mix": 0.35
        }
      ]
    }
  ]
};

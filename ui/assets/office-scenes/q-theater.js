export default {
  "version": 2,
  "name": "乘凉小插曲",
  "duration": 12.5,
  "actors": [
    {
      "id": "mano",
      "x": 0.36,
      "view": "full",
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
            "tracks": {},
            "modelKind": "cb",
            "name": "mano-bird",
            "sourceEnd": 10.5
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
      ],
      "flip": true
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
            "duration": 1.2,
            "fps": 30,
            "loop": true,
            "tracks": {},
            "modelKind": "cb",
            "name": "tenka-startle",
            "sourceEnd": 3.2
          },
          "mix": 0.12
        },
        {
          "at": 7.1,
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
          "at": 7.8,
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

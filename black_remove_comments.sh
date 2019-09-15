#!/bin/bash

REMOTE="upstream"
BRANCH="master"


function comment_black_failures()
{
    sed -i "s|class InvalidResponseError(Exception):|# class InvalidResponseError(Exception):|g" $1
    sed -i "s|class InvalidChecksumError(Exception):|# class InvalidChecksumError(Exception):|g" $1
    sed -i "s|class InterleavedDataError(Exception):|# class InterleavedDataError(Exception):|g" $1
    sed -i "s|class FastbootTransferError(usb_exceptions.FormatMessageWithArgumentsException):|# class FastbootTransferError(usb_exceptions.FormatMessageWithArgumentsException):|g" $1
    sed -i "s|class FastbootRemoteFailure(usb_exceptions.FormatMessageWithArgumentsException):|# class FastbootRemoteFailure(usb_exceptions.FormatMessageWithArgumentsException):|g" $1
    sed -i "s|class FastbootStateMismatch(usb_exceptions.FormatMessageWithArgumentsException):|# class FastbootStateMismatch(usb_exceptions.FormatMessageWithArgumentsException):|g" $1
    sed -i "s|class FastbootInvalidResponse(|# class FastbootInvalidResponse(|g" $1
    sed -i "s|    usb_exceptions.FormatMessageWithArgumentsException):|#     usb_exceptions.FormatMessageWithArgumentsException):|g" $1
    sed -i "s|class PushFailedError(Exception):|# class PushFailedError(Exception):|g" $1
    sed -i "s|class PullFailedError(Exception):|# class PullFailedError(Exception):|g" $1
}

function uncomment_black_failures()
{
    sed -i "s|# class InvalidResponseError(Exception):|class InvalidResponseError(Exception):|g" $1
    sed -i "s|# class InvalidChecksumError(Exception):|class InvalidChecksumError(Exception):|g" $1
    sed -i "s|# class InterleavedDataError(Exception):|class InterleavedDataError(Exception):|g" $1
    sed -i "s|# class FastbootTransferError(usb_exceptions.FormatMessageWithArgumentsException):|class FastbootTransferError(usb_exceptions.FormatMessageWithArgumentsException):|g" $1
    sed -i "s|# class FastbootRemoteFailure(usb_exceptions.FormatMessageWithArgumentsException):|class FastbootRemoteFailure(usb_exceptions.FormatMessageWithArgumentsException):|g" $1
    sed -i "s|# class FastbootStateMismatch(usb_exceptions.FormatMessageWithArgumentsException):|class FastbootStateMismatch(usb_exceptions.FormatMessageWithArgumentsException):|g" $1
    sed -i "s|# class FastbootInvalidResponse(|# class FastbootInvalidResponse(|g" $1
    sed -i "s|#     usb_exceptions.FormatMessageWithArgumentsException):|    usb_exceptions.FormatMessageWithArgumentsException):|g" $1
    sed -i "s|# class PushFailedError(Exception):|class PushFailedError(Exception):|g" $1
    sed -i "s|# class PullFailedError(Exception):|class PullFailedError(Exception):|g" $1
}


git fetch $REMOTE

# 1. Checkout the current files on upstream/master
# 2. Remove comments
# 3. Apply black code formatting

# `adb` directory
for pyfile in $(ls adb/*.py); do
    git checkout $REMOTE/$BRANCH -- $pyfile

    python remove_comments.py $pyfile
    rm $pyfile
    mv "$pyfile,strip" $pyfile
    python remove_blank_lines.py $pyfile

    comment_black_failures $pyfile
    black $pyfile --fast
    uncomment_black_failures $pyfile
done

# `test` directory
for pyfile in $(ls test/*.py); do
    git checkout $REMOTE/$BRANCH -- $pyfile

    python remove_comments.py $pyfile
    rm $pyfile
    mv "$pyfile,strip" $pyfile
    python remove_blank_lines.py $pyfile

    comment_black_failures $pyfile
    black $pyfile --fast
    uncomment_black_failures $pyfile
done

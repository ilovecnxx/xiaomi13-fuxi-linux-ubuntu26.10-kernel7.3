/* SPDX-License-Identifier: MIT */
/* Exercise repeated starts on one configured Camera with real request status checks. */
#include <libcamera/libcamera.h>

#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <dlfcn.h>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

using namespace libcamera;
using namespace std::chrono_literals;

class Capture
{
public:
    explicit Capture(std::shared_ptr<Camera> camera) : camera_(std::move(camera)) {}

    void done(Request *request)
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (request->status() == Request::RequestCancelled)
            return;
        completed_++;
        for (const auto &[stream, buffer] : request->buffers()) {
            (void)stream;
            const auto &metadata = buffer->metadata();
            if (metadata.status == FrameMetadata::FrameError) {
                errors_++;
                for (const auto &plane : metadata.planes())
                    invalid_ |= plane.bytesused != 0;
            } else {
                invalid_ |= metadata.status != FrameMetadata::FrameSuccess;
                for (const auto &plane : metadata.planes())
                    invalid_ |= plane.bytesused == 0;
            }
        }
        if (!stopping_ && submitted_ < target_) {
            request->reuse(Request::ReuseBuffers);
            invalid_ |= camera_->queueRequest(request) != 0;
            submitted_++;
        }
        cv_.notify_all();
    }

    bool run(const std::vector<std::unique_ptr<Request>> &requests, unsigned int frames)
    {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            completed_ = submitted_ = errors_ = 0;
            target_ = frames;
            invalid_ = stopping_ = false;
            for (const auto &request : requests) {
                if (submitted_ == target_)
                    break;
                request->reuse(Request::ReuseBuffers);
                invalid_ |= camera_->queueRequest(request.get()) != 0;
                submitted_++;
            }
        }
        bool complete;
        {
            std::unique_lock<std::mutex> lock(mutex_);
            complete = cv_.wait_for(lock, 30s, [&] { return invalid_ || completed_ == target_; });
            stopping_ = true;
        }
        int stopped = camera_->stop();
        std::cout << "CAPTURE completed=" << completed_ << " expected=" << frames
                  << " errors=" << errors_ << " invalid=" << invalid_ << " stop=" << stopped << std::endl;
        return complete && completed_ == frames && !invalid_ && !stopped;
    }

    unsigned int errors() const { return errors_; }

private:
    std::shared_ptr<Camera> camera_;
    std::mutex mutex_;
    std::condition_variable cv_;
    unsigned int completed_ = 0, submitted_ = 0, target_ = 0, errors_ = 0;
    bool stopping_ = false, invalid_ = false;
};

static unsigned int number(const char *text)
{
    std::string input(text);
    size_t used;
    unsigned long value = std::stoul(input, &used);
    if (used != input.size() || value > 10000 || input.front() == '-')
        throw std::invalid_argument("invalid count");
    return value;
}

int main(int argc, char **argv)
try {
    if (argc != 5 && argc != 7) {
        std::cerr << "usage: restart-camera ROUNDS FRAMES EXPECTED_START_FAILURES EXPECTED_ERROR_FRAMES [WIDTH HEIGHT]\n";
        return 2;
    }
    unsigned int rounds = number(argv[1]), frames = number(argv[2]);
    unsigned int expectedFailures = number(argv[3]), expectedErrors = number(argv[4]);
    if (!rounds || !frames || expectedFailures >= rounds)
        return 2;
    auto checkpoint = reinterpret_cast<int (*)()>(dlsym(RTLD_DEFAULT, "fuxi_egl_checkpoint"));
    if (!checkpoint) {
        std::cerr << "EGL context audit library must be preloaded\n";
        return 2;
    }
    CameraManager manager;
    if (manager.start() || manager.cameras().size() != 1)
        return 1;
    auto camera = manager.cameras().front();
    auto model = camera->properties().get(properties::Model);
    if (!model || *model != "ov13b10" || camera->acquire())
        return 1;
    auto config = camera->generateConfiguration({StreamRole::Viewfinder});
    if (!config || config->empty())
        return 1;
    config->at(0).size = argc == 7 ? Size(number(argv[5]), number(argv[6])) : Size(2104, 1560);
    if (config->validate() == CameraConfiguration::Invalid || camera->configure(config.get()))
        return 1;
    bool valid = true;
    unsigned int failures = 0, errors = 0;
    {
        FrameBufferAllocator allocator(camera);
        Stream *stream = config->at(0).stream();
        if (allocator.allocate(stream) <= 0)
            return 1;
        std::vector<std::unique_ptr<Request>> requests;
        for (const auto &buffer : allocator.buffers(stream)) {
            auto request = camera->createRequest();
            if (!request || request->addBuffer(stream, buffer.get()))
                return 1;
            requests.push_back(std::move(request));
        }
        Capture capture(camera);
        camera->requestCompleted.connect(&capture, &Capture::done);
        for (unsigned int round = 0; round < rounds; round++) {
            int ret = camera->start();
            if (ret) {
                failures++;
                std::cout << "START_FAILED round=" << round << " error=" << ret << std::endl;
            } else {
                bool captured = capture.run(requests, frames);
                errors += capture.errors();
                if (!captured) {
                    checkpoint();
                    valid = false;
                    break;
                }
            }
            valid &= checkpoint() == 0;
        }
        camera->requestCompleted.disconnect(&capture);
    }
    valid &= camera->release() == 0;
    camera.reset();
    manager.stop();
    valid &= failures == expectedFailures && errors == expectedErrors;
    std::cout << "RESTART rounds=" << rounds << " start_failures=" << failures
              << " error_frames=" << errors << " result=" << (valid ? "PASS" : "FAIL") << std::endl;
    return valid ? 0 : 1;
} catch (const std::exception &error) {
    std::cerr << error.what() << '\n';
    return 2;
}

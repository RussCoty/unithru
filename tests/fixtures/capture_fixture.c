#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define EXPORTED __attribute__((visibility("default"), noinline))

EXPORTED int IOConnectCallMethod(uint32_t connection, uint32_t selector,
    const uint64_t *input, uint32_t input_count, const void *input_struct, size_t input_size,
    uint64_t *output, uint32_t *output_count, void *output_struct, size_t *output_size)
{
    if (selector == 99) return 5;
    if (output && output_count && *output_count && input_count) {
        output[0] = input[0];
        *output_count = 1;
    }
    if (output_struct && output_size && *output_size >= input_size) {
        memcpy(output_struct, input_struct, input_size);
        *output_size = input_size;
    }
    return 0;
}

EXPORTED int IOConnectCallScalarMethod(uint32_t connection, uint32_t selector,
    const uint64_t *input, uint32_t input_count, uint64_t *output, uint32_t *output_count)
{
    return IOConnectCallMethod(connection, selector, input, input_count, NULL, 0, output, output_count, NULL, NULL);
}

EXPORTED int IOConnectCallStructMethod(uint32_t connection, uint32_t selector,
    const void *input, size_t input_size, void *output, size_t *output_size)
{
    return IOConnectCallMethod(connection, selector, NULL, 0, input, input_size, NULL, NULL, output, output_size);
}

EXPORTED int libusb_control_transfer(void *handle, uint8_t request_type, uint8_t request,
    uint16_t value, uint16_t index, unsigned char *data, uint16_t length, unsigned int timeout)
{
    if (request == 99) return -1;
    if ((request_type & 128) && length) data[0] = (unsigned char)value;
    return length;
}

EXPORTED int libusb_bulk_transfer(void *handle, unsigned char endpoint, unsigned char *data,
    int length, int *transferred, unsigned int timeout)
{
    *transferred = length;
    return 0;
}

EXPORTED int libusb_interrupt_transfer(void *handle, unsigned char endpoint, unsigned char *data,
    int length, int *transferred, unsigned int timeout)
{
    return libusb_bulk_transfer(handle, endpoint, data, length, transferred, timeout);
}

int main(void)
{
    char line[128];
    unsigned char counter = 0;
    printf("ready %d\n", getpid());
    fflush(stdout);
    while (fgets(line, sizeof line, stdin)) {
        char operation[32];
        unsigned int value = 0;
        sscanf(line, "%31s %u", operation, &value);
        uint64_t input = value, output = 0;
        uint32_t output_count = 1;
        unsigned char data[4] = {0xa0, 1, (unsigned char)value, counter++};
        unsigned char response[4] = {0};
        size_t response_size = sizeof response;
        if (!strcmp(operation, "quit")) break;
        if (!strcmp(operation, "scalar"))
            IOConnectCallScalarMethod(17, 1, &input, 1, &output, &output_count);
        else if (!strcmp(operation, "struct"))
            IOConnectCallStructMethod(17, 2, data, sizeof data, response, &response_size);
        else if (!strcmp(operation, "iokit"))
            IOConnectCallMethod(17, 3, &input, 1, data, sizeof data, &output, &output_count, response, &response_size);
        else if (!strcmp(operation, "fail"))
            IOConnectCallMethod(17, 99, &input, 1, data, sizeof data, &output, &output_count, response, &response_size);
        else if (!strcmp(operation, "usb"))
            libusb_control_transfer((void *)17, 0x40, 1, value, 0, data, sizeof data, 100);
        else if (!strcmp(operation, "bulk")) {
            int actual = 0;
            libusb_bulk_transfer((void *)17, 2, data, sizeof data, &actual, 100);
        }
        printf("done\n");
        fflush(stdout);
    }
    return 0;
}
